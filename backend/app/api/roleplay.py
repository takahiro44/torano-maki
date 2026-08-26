"""ロープレのエンドポイント。担当: CLAUDE.md 1.1 を参照。

社内の実際の発話を根拠に、判断が必要な一場面だけを短時間で練習する。

ロジックは `services/roleplay.py` にある。ここは HTTP の入口と、
業務エラー・LLM側の失敗をステータスコードへ振り分けることだけを行う
（`api/chat.py` と同じ分担）。

**応答に時間がかかる。** セッション作成は検索とシナリオ生成、
ターン進行は顧客役の生成でそれぞれ vLLM を待つ。
クライアント側のタイムアウトは余裕を持たせること。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.roleplay import (
    CATEGORY_LABELS,
    CategoryOption,
    LearnerTurnRequest,
    RoleplaySession,
    RoleplaySessionCreate,
    RoleplayTranscription,
)
from app.services.audio_upload import AudioUploadError, resolve_suffix, temporary_audio
from app.services.llm_client import LlmNotConfiguredError, LlmRequestError
from app.services.roleplay import (
    RoleplayError,
    RoleplayGenerationError,
    add_learner_turn,
    build_session_view,
    create_feedback,
    create_session,
    ensure_can_answer,
    get_session,
    retry_session,
)
from app.services.transcription import (
    EmptyTranscriptError,
    SttNotConfiguredError,
    SttRequestError,
    SttResponseError,
    transcribe,
)

router = APIRouter(prefix="/roleplay", tags=["roleplay"])

DbSession = Annotated[Session, Depends(get_db)]

# 業務エラーの意味をHTTPへ対応づける。
#
# **409 と 422 を分けている。** 409 は「今のセッションの状態では
# その操作ができない」（発言回数の上限、終了済み）で、やり直せば済む。
# 422 は「その入力では練習を始められない」（根拠付きナレッジが無い）で、
# 先にナレッジを登録する必要がある。画面の案内が変わるため混ぜない。
_ERROR_STATUS: dict[str, int] = {
    "session_not_found": 404,
    "no_evidence": 422,
    "session_not_active": 409,
    "max_turns_reached": 409,
    "no_learner_turn": 409,
    "invalid_scenario": 500,
}

# 受け取る音声の上限。
#
# 商談1本（`/ingest/audio` の200MB）とは用途が違う。ここへ来るのは
# 後輩の1回の回答で、長くても1分程度。桁を合わせておかないと、
# 商談音声を誤ってこちらへ投げたときに数十秒待たされてから気づくことになる。
_MAX_TURN_AUDIO_BYTES = 25 * 1024 * 1024

# 短い回答の文字起こしにかかる上限。商談1本用の600秒は長すぎる。
_TURN_STT_TIMEOUT = 120.0


def _to_http(exc: RoleplayError) -> HTTPException:
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 400), detail=exc.message)


def _upload_to_http(exc: AudioUploadError) -> HTTPException:
    status = 413 if exc.code == "too_large" else 400
    return HTTPException(status_code=status, detail=exc.message)


@router.get("/categories", response_model=list[CategoryOption])
def list_categories() -> list[CategoryOption]:
    """練習できる場面の一覧。

    画面のボタンをこの応答から作る。フロントに同じ対応表を持たせると、
    片方だけ増やしたときに選べない場面が生まれる。
    """
    return [CategoryOption(key=key, label=label) for key, label in CATEGORY_LABELS.items()]


@router.post("/sessions", response_model=RoleplaySession, status_code=201)
def start_session(payload: RoleplaySessionCreate, db: DbSession) -> RoleplaySession:
    """質問・カテゴリ・AIチャットのCitationから練習を始める。

    **30秒以上かかる。** 検索・根拠取得・シナリオ生成を順に行うため。
    """
    try:
        session = create_session(db, payload)
    except RoleplayError as exc:
        raise _to_http(exc) from exc
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (LlmRequestError, RoleplayGenerationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return build_session_view(db, session)


@router.get("/sessions/{session_id}", response_model=RoleplaySession)
def read_session(session_id: UUID, db: DbSession) -> RoleplaySession:
    """画面の再読込とデバッグ用。シナリオ・発言・出典・振り返りを全て返す。"""
    try:
        session = get_session(db, session_id)
    except RoleplayError as exc:
        raise _to_http(exc) from exc
    return build_session_view(db, session)


@router.post("/sessions/{session_id}/turns/text", response_model=RoleplaySession)
def submit_text_turn(
    session_id: UUID, payload: LearnerTurnRequest, db: DbSession
) -> RoleplaySession:
    """後輩の回答を送り、顧客役の返答まで進める。

    音声で答えた場合も、STT結果を人が確認・修正してからここへ送る。
    文字起こしの誤りを直す段を残すため、音声のまま顧客返答まで進めない
    （計画書8章）。
    """
    try:
        session = add_learner_turn(db, get_session(db, session_id), payload)
    except RoleplayError as exc:
        raise _to_http(exc) from exc
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (LlmRequestError, RoleplayGenerationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return build_session_view(db, session)


@router.post("/sessions/{session_id}/feedback", response_model=RoleplaySession)
def finish_session(session_id: UUID, db: DbSession) -> RoleplaySession:
    """振り返りを作ってセッションを終了する。

    発言回数が残っていても呼べる。「もう十分」と思った時点で
    振り返れる方が、上限まで話させるより練習として速い。
    """
    try:
        session = get_session(db, session_id)
        create_feedback(db, session)
    except RoleplayError as exc:
        raise _to_http(exc) from exc
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (LlmRequestError, RoleplayGenerationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return build_session_view(db, session)


@router.post("/sessions/{session_id}/retry", response_model=RoleplaySession, status_code=201)
def retry(session_id: UUID, db: DbSession) -> RoleplaySession:
    """同じ場面をもう一度。シナリオは作り直さないため待ち時間がない。"""
    try:
        session = retry_session(db, get_session(db, session_id))
    except RoleplayError as exc:
        raise _to_http(exc) from exc
    return build_session_view(db, session)


@router.post("/sessions/{session_id}/turns/audio", response_model=RoleplayTranscription)
def transcribe_turn_audio(
    session_id: UUID,
    db: DbSession,
    file: Annotated[UploadFile, File(description="マイク録音（webm / wav など）")],
) -> RoleplayTranscription:
    """マイク回答を文字起こしして返す。**ここでは発言として保存しない。**

    顧客の返答まで進めないのは、STTの誤認識を直す段を残すためである
    （計画書8章）。誤認識のまま進むと、顧客役が誤った内容へ答え、
    フィードバックまでその前提で作られてしまう。
    利用者が画面で確認・修正してから `turns/text` へ送る。

    **`async def` にしないこと。** 中で同期のHTTPクライアントを使うため、
    async にするとイベントループを数十秒ブロックしてサーバ全体が止まる
    （`api/ingest.py` と同じ理由）。
    """
    # 文字起こしを始める前に状態を確かめる。数十秒待たせた後で
    # 「もう発言できません」と返すのは、待たせた分だけ体験が悪い
    try:
        ensure_can_answer(db, get_session(db, session_id))
        suffix = resolve_suffix(file.filename, file.content_type)
    except RoleplayError as exc:
        raise _to_http(exc) from exc
    except AudioUploadError as exc:
        raise _upload_to_http(exc) from exc

    try:
        with temporary_audio(file.file, suffix=suffix, max_bytes=_MAX_TURN_AUDIO_BYTES) as path:
            transcript = transcribe(path, timeout=_TURN_STT_TIMEOUT)
    except AudioUploadError as exc:
        raise _upload_to_http(exc) from exc
    except SttNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EmptyTranscriptError as exc:
        # 送られた音声の問題なので、サーバ障害（502）と区別する
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (SttRequestError, SttResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return RoleplayTranscription(
        text=transcript.text,
        language=transcript.language,
        duration_sec=transcript.segments[-1].end if transcript.segments else 0.0,
    )


# TODO: Phase 3 の残り（PR: feat/roleplay-voice）
#   POST /roleplay/speech  顧客役の返答をTTSで読み上げる
#   → `google-cloud-texttospeech` の依存追加にチーム合意が必要（CLAUDE.md 3章）
