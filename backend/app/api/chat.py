"""AIチャットのエンドポイント。

`/search` と分けているのは責務が違うため。`/search` は「検索結果を返す」
部品であり、`/chat` はそれを **Tool として使う** 側にある。
同じルーターに混ぜると依存の向きが読めなくなり、担当も分けられない。

ロジックは services/agent_loop.py にある。ここは HTTP の入口と、
LLM 側の失敗をステータスコードへ振り分けることだけを行う。
"""

import logging
from collections.abc import Iterator
from contextlib import closing
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.chat import (
    ChatRequest,
    ChatResponse,
    ChatStreamErrorCode,
    ChatStreamErrorEvent,
    ChatStreamEvent,
    ChatTranscription,
)
from app.services.agent_loop import run_agent_loop
from app.services.agent_stream import stream_agent_answer
from app.services.agent_tools import AGENT_TOOL_DEFINITIONS
from app.services.audio_upload import AudioUploadError, resolve_suffix, temporary_audio
from app.services.llm_client import LlmNotConfiguredError, LlmRequestError
from app.services.transcription import (
    EmptyTranscriptError,
    SttNotConfiguredError,
    SttRequestError,
    SttResponseError,
    transcribe,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

DbSession = Annotated[Session, Depends(get_db)]


class EventStreamResponse(StreamingResponse):
    """OpenAPI に「本文は SSE」と書かせるためだけの型。

    StreamingResponse は media_type を持たないため、そのままだと
    FastAPI が本文を application/json として載せてしまい、
    フロントが生成する型が実物とずれる。
    """

    media_type = "text/event-stream"


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest, db: DbSession) -> ChatResponse:
    """蓄積ナレッジをもとに質問へ答える。

    **応答に10秒以上かかる。** Agent が検索・根拠取得を挟むため、
    1リクエストで vLLM と複数回やりとりする。
    クライアント側のタイムアウトは余裕を持たせること（180秒以上を推奨）。

    会話履歴はサーバに残らない。**毎回すべての履歴を送ること**
    （理由は models/chat.py）。
    """
    try:
        result = run_agent_loop(db, payload.messages, top_k=payload.top_k)
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatResponse(
        answer=result.answer,
        citations=result.citations,
        tool_trace=result.tool_trace,
        usage=result.usage,
    )


@router.get("/tools")
def list_tools() -> list[dict[str, Any]]:
    """Agent に公開している Tool の定義を返す。

    フロント担当が「AIが何をできるのか」をコードを読まずに確認できるようにする。
    Tool を増減したときの実際の姿がここに出るため、資料より先に真実になる。
    """
    return AGENT_TOOL_DEFINITIONS


# 受け取る音声の上限。
#
# ここへ来るのは話しかけた質問1つで、長くても1分程度。商談1本用の200MB
# （`/ingest/audio/transcribe`）と桁を合わせると、商談音声を誤ってこちらへ
# 投げたときに数十秒待たされてから気づくことになる。
_MAX_QUESTION_AUDIO_BYTES = 25 * 1024 * 1024

# 短い質問の文字起こしにかかる上限。商談1本用の600秒は長すぎる。
_QUESTION_STT_TIMEOUT = 120.0


@router.post("/voice", response_model=ChatTranscription)
def transcribe_question(
    file: Annotated[UploadFile, File(description="マイク録音（webm / wav など）")],
) -> ChatTranscription:
    """話した質問を文字起こしして返す。**保存も回答もしない。**

    **`/ingest/audio/transcribe` を使い回さない。** あちらは商談音声を
    ナレッジの材料として取り込む口で、`data_sources` と `utterance_segments` に
    行を作る。チャットの質問は出典ではないため、そこへ混ぜると
    出典一覧に「質問だけの商談音声」が溜まり、Citation の意味が壊れる。

    **文字起こしをそのまま質問として実行しない。** 結果は入力欄に入るだけで、
    送るかどうかは人が決める。STTは誤認識するため、確認の段を挟まないと
    誤った質問のまま Agent が検索してしまう（`roleplay` のマイク回答と同じ判断）。

    **`async def` にしないこと。** 中で同期のHTTPクライアントを使うため、
    async にするとイベントループを数十秒ブロックしてサーバ全体が止まる
    （`api/ingest.py` と同じ理由）。
    """
    try:
        suffix = resolve_suffix(file.filename, file.content_type)
        with temporary_audio(file.file, suffix=suffix, max_bytes=_MAX_QUESTION_AUDIO_BYTES) as path:
            transcript = transcribe(path, timeout=_QUESTION_STT_TIMEOUT)
    except AudioUploadError as exc:
        # 利用者の操作で直せる問題。サーバ障害と区別する
        raise HTTPException(
            status_code=413 if exc.code == "too_large" else 400, detail=exc.message
        ) from exc
    except SttNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EmptyTranscriptError as exc:
        # 送られた音声の問題なので、サーバ障害（502）と区別する
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (SttRequestError, SttResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatTranscription(
        text=transcript.text,
        language=transcript.language,
        duration_sec=transcript.segments[-1].end if transcript.segments else 0.0,
    )


# SSE はプロキシに溜め込まれると1トークンずつ流す意味が消える。
# 中間に nginx が入っても素通しさせるためのヘッダ
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post(
    "/stream",
    response_class=EventStreamResponse,
    responses={
        200: {
            "model": ChatStreamEvent,
            "description": "SSE。1イベント = `data: <ChatStreamEvent のJSON>\\n\\n`",
        }
    },
)
def chat_stream(payload: ChatRequest, db: DbSession) -> EventStreamResponse:
    """`/chat` と同じ質問に、進捗と回答をSSEで流しながら答える。

    **既存の `/chat` を置き換えない。** 応答を待ち切る呼び出し元のために残す。

    実測で総時間の大半は回答を書いている時間だった（decode 約20 tok/s）。
    書き終わるのを待たずに届いた分から流すことが、体感を変える唯一の手段。

    **失敗も 200 で返る。** ヘッダは最初のイベントより前に送られるため、
    そのあとの失敗をステータスコードで表現できない。`error` イベントで流す。
    """

    def _events() -> Iterator[str]:
        try:
            # 利用者が中止すると、この関数は yield の途中で閉じられる。
            # closing で Agent 側のジェネレータまで確実に閉じ、
            # vLLM への接続を残さない
            with closing(stream_agent_answer(db, payload.messages, top_k=payload.top_k)) as events:
                for event in events:
                    yield _sse(event)
        except LlmNotConfiguredError as exc:
            yield _sse_error(ChatStreamErrorCode.LLM_NOT_CONFIGURED, str(exc))
        except LlmRequestError as exc:
            yield _sse_error(ChatStreamErrorCode.LLM_UNREACHABLE, str(exc))
        except Exception:
            # 想定外を握り潰さずログに残す。画面には内部事情を出さない
            logger.exception("チャットのストリーミング中に想定外のエラーが発生しました")
            yield _sse_error(ChatStreamErrorCode.INTERNAL, "想定外のエラーが発生しました")

    return EventStreamResponse(_events(), headers=_SSE_HEADERS)


def _sse(event: BaseModel) -> str:
    """1イベントをSSEの1レコードにする。

    `event:` フィールドを使わないのは契約どおり。JSON側の `type` で判別させ、
    フロントに型ごとのリスナーを生やさせない。
    """
    return f"data: {event.model_dump_json()}\n\n"


def _sse_error(code: ChatStreamErrorCode, message: str) -> str:
    return _sse(ChatStreamErrorEvent(code=code, message=message))
