"""上司レビュー: AIチャットの要約→上司へ送信→回答をナレッジ化。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.chat_review import (
    ChatReviewDetail,
    ChatReviewListItem,
    ChatReviewSummary,
    CreatedKnowledgeItem,
    RespondChatReviewRequest,
    SummarizeChatReviewRequest,
)
from app.models.tables import ChatReviewTable
from app.services.chat_review import (
    create_chat_review,
    generate_chat_review_summary,
    get_created_knowledge,
    respond_to_chat_review,
)
from app.services.extraction import LlmNotConfiguredError, LlmRequestError

router = APIRouter(prefix="/chat-reviews", tags=["chat-reviews"])

DbSession = Annotated[Session, Depends(get_db)]


def _to_detail(row: ChatReviewTable, db: Session) -> ChatReviewDetail:
    created = get_created_knowledge(row.answered_data_source_id, db)
    detail = ChatReviewDetail.model_validate(row)
    detail.created_knowledge = [CreatedKnowledgeItem(id=k.id, title=k.title) for k in created]
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


@router.post("", response_model=ChatReviewDetail, status_code=status.HTTP_201_CREATED)
def send_chat_review(payload: SummarizeChatReviewRequest, db: DbSession) -> ChatReviewDetail:
    """「上司に送信」ボタン。要約を再生成してpendingで保存する。"""
    try:
        row = create_chat_review(payload.messages, db)
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
