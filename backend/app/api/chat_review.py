"""上司レビュー: AIチャットの要約→上司へ送信→回答をナレッジ化。"""

import logging
from collections.abc import Iterator
from contextlib import closing
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.chat import ChatStreamErrorCode
from app.models.chat_review import (
    ChatReviewDetail,
    ChatReviewListItem,
    ChatReviewSummary,
    RespondChatReviewRequest,
    ReviewStreamErrorEvent,
    ReviewStreamEvent,
    SendChatReviewRequest,
    SummarizeChatReviewRequest,
)
from app.models.knowledge import Knowledge
from app.models.tables import ChatReviewTable
from app.services.chat_review import (
    create_chat_review,
    generate_chat_review_summary,
    get_created_knowledge,
    respond_to_chat_review,
)
from app.services.chat_review_stream import stream_chat_review_diagnosis
from app.services.extraction import LlmNotConfiguredError, LlmRequestError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat-reviews", tags=["chat-reviews"])

DbSession = Annotated[Session, Depends(get_db)]


class EventStreamResponse(StreamingResponse):
    """OpenAPI に「本文は SSE」と書かせるためだけの型（chat.py と同じ理由）。"""

    media_type = "text/event-stream"


# SSE はプロキシに溜め込まれると流す意味が消える
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _to_detail(row: ChatReviewTable, db: Session) -> ChatReviewDetail:
    created = get_created_knowledge(row.answered_data_source_id, db)
    detail = ChatReviewDetail.model_validate(row)
    detail.created_knowledge = [Knowledge.model_validate(k) for k in created]
    # 名乗った名前は問いに入っている（列を足さないため）。上司の画面が
    # 配列の先頭を覗きに行かなくて済むよう、ここで1つに寄せる
    detail.learner_name = next((q.asked_by for q in detail.knowledge_gaps if q.asked_by), None)
    return detail


@router.post("/summarize", response_model=ChatReviewSummary)
def summarize_chat_review(payload: SummarizeChatReviewRequest) -> ChatReviewSummary:
    """「まとめる」ボタン。DBには書き込まない。"""
    try:
        return generate_chat_review_summary(payload.messages)
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/summarize/stream",
    response_class=EventStreamResponse,
    responses={
        200: {
            "model": ReviewStreamEvent,
            "description": "SSE。1イベント = `data: <ReviewStreamEvent のJSON>\\n\\n`",
        }
    },
)
def summarize_chat_review_stream(payload: SummarizeChatReviewRequest, db: DbSession):
    """「まとめる」を、工程ごとに実況しながら行う。DBには書き込まない。

    **既存の `/summarize` を置き換えない。** 応答を待ち切る呼び出し元のために残す。

    要約1往復に数十秒、そのあと疑問点ごとにナレッジDBを引く。
    どちらも実際に時間がかかる工程であり、終わるまで無言にしないためにSSEで流す。

    **失敗も 200 で返る。** ヘッダは最初のイベントより前に送られるため、
    そのあとの失敗をステータスコードで表現できない。`error` イベントで流す。
    """

    def _events() -> Iterator[str]:
        try:
            # 中止されるとこの関数は yield の途中で閉じられる。
            # closing で下のジェネレータまで確実に閉じ、vLLM への接続を残さない
            with closing(stream_chat_review_diagnosis(db, payload.messages)) as events:
                for event in events:
                    yield _sse(event)
        except LlmNotConfiguredError as exc:
            yield _sse_error(ChatStreamErrorCode.LLM_NOT_CONFIGURED, str(exc))
        except LlmRequestError as exc:
            yield _sse_error(ChatStreamErrorCode.LLM_UNREACHABLE, str(exc))
        except Exception:
            logger.exception("レビュー要約のストリーミング中に想定外のエラーが発生しました")
            yield _sse_error(ChatStreamErrorCode.INTERNAL, "想定外のエラーが発生しました")

    return EventStreamResponse(_events(), headers=_SSE_HEADERS)


def _sse(event: BaseModel) -> str:
    """1イベントをSSEの1レコードにする。`event:` は使わず JSON側の `type` で判別させる。"""
    return f"data: {event.model_dump_json()}\n\n"


def _sse_error(code: ChatStreamErrorCode, message: str) -> str:
    return _sse(ReviewStreamErrorEvent(code=code, message=message))


@router.post("", response_model=ChatReviewDetail, status_code=status.HTTP_201_CREATED)
def send_chat_review(payload: SendChatReviewRequest, db: DbSession) -> ChatReviewDetail:
    """「上司に質問する」ボタン。pendingで保存する。

    `hearing` が付いていれば、後輩が実際に見て答えた内容をそのまま保存する
    （services/chat_review.py の理由）。付いていなければ従来どおり
    サーバ側で要約を生成する。
    """
    try:
        row = create_chat_review(payload, db)
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _to_detail(row, db)


@router.get("", response_model=list[ChatReviewListItem])
def list_chat_reviews(
    db: DbSession, status_filter: str | None = Query(default=None, alias="status")
) -> list[ChatReviewTable]:
    stmt = select(ChatReviewTable).order_by(ChatReviewTable.created_at.desc())
    if status_filter is not None:
        stmt = stmt.where(ChatReviewTable.status == status_filter)
    return list(db.execute(stmt).scalars().all())


@router.get("/{review_id}", response_model=ChatReviewDetail)
def get_chat_review(review_id: UUID, db: DbSession) -> ChatReviewDetail:
    row = db.execute(
        select(ChatReviewTable).where(ChatReviewTable.id == review_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="ChatReview が見つかりません")
    return _to_detail(row, db)


@router.post("/{review_id}/respond", response_model=ChatReviewDetail)
def respond_chat_review(
    review_id: UUID, payload: RespondChatReviewRequest, db: DbSession
) -> ChatReviewDetail:
    try:
        row = respond_to_chat_review(review_id, payload.response_text, db)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _to_detail(row, db)
