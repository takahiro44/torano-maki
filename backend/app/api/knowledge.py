"""ナレッジのCRUD。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.knowledge import (
    ExtractRequest,
    Knowledge,
    KnowledgeCreate,
    KnowledgeStatus,
    KnowledgeStatusPatch,
    KnowledgeUpdate,
)
from app.models.tables import DataSourceTable, KnowledgeTable
from app.services.embedding import generate_embedding
from app.services.extraction import (
    LlmNotConfiguredError,
    LlmRequestError,
    process_text_to_knowledge,
)
from app.services.search_text import generate_search_text

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DbSession = Annotated[Session, Depends(get_db)]

_CBR_KEYS = (
    "title",
    "situation",
    "customer_issue",
    "sales_action",
    "action_reason",
    "result",
    "learning",
)


def _get_or_404(db: Session, knowledge_id: UUID) -> KnowledgeTable:
    row = db.execute(
        select(KnowledgeTable).where(
            KnowledgeTable.id == knowledge_id,
            KnowledgeTable.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge が見つかりません")
    return row


def _refresh_search_fields(row: KnowledgeTable) -> None:
    row.search_text = generate_search_text(
        title=row.title,
        situation=row.situation,
        customer_issue=row.customer_issue,
        sales_action=row.sales_action,
        action_reason=row.action_reason,
        result=row.result,
        learning=row.learning,
    )
    row.embedding = generate_embedding(row.search_text)


@router.post("/extract", response_model=list[Knowledge])
def extract_and_store(payload: ExtractRequest, db: DbSession) -> list[KnowledgeTable]:
    """テキストから CBR ナレッジを抽出し draft で格納する。"""
    try:
        saved, _notes = process_text_to_knowledge(
            payload.text, db, data_source_id=payload.data_source_id
        )
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return saved


@router.post("", response_model=Knowledge, status_code=status.HTTP_201_CREATED)
def create_knowledge(payload: KnowledgeCreate, db: DbSession) -> KnowledgeTable:
    data_source_id = payload.data_source_id
    if data_source_id is None:
        source = DataSourceTable(source_type="manual")
        db.add(source)
        db.flush()
        data_source_id = source.id

    dump = payload.model_dump()
    dump["data_source_id"] = data_source_id
    dump["status"] = payload.status.value if hasattr(payload.status, "value") else payload.status
    row = KnowledgeTable(**dump)
    _refresh_search_fields(row)
    db.add(row)
    db.commit()
    return row


@router.get("", response_model=list[Knowledge])
def list_knowledge(
    db: DbSession,
    status_filter: Annotated[KnowledgeStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[KnowledgeTable]:
    stmt = select(KnowledgeTable).where(KnowledgeTable.deleted_at.is_(None))
    if status_filter is not None:
        stmt = stmt.where(KnowledgeTable.status == status_filter)
    stmt = stmt.order_by(KnowledgeTable.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


@router.get("/count")
def count_knowledge(db: DbSession) -> dict[str, int]:
    rows = db.execute(
        select(KnowledgeTable.status, func.count())
        .where(KnowledgeTable.deleted_at.is_(None))
        .group_by(KnowledgeTable.status)
    ).all()
    counts = {s.value: 0 for s in KnowledgeStatus}
    for name, n in rows:
        counts[name] = n
    counts["total"] = sum(counts.values())
    return counts


@router.get("/{knowledge_id}", response_model=Knowledge)
def get_knowledge(knowledge_id: UUID, db: DbSession) -> KnowledgeTable:
    return _get_or_404(db, knowledge_id)


@router.patch("/{knowledge_id}/status", response_model=Knowledge)
def patch_knowledge_status(
    knowledge_id: UUID, payload: KnowledgeStatusPatch, db: DbSession
) -> KnowledgeTable:
    row = _get_or_404(db, knowledge_id)
    row.status = payload.status.value
    db.commit()
    return row


@router.patch("/{knowledge_id}", response_model=Knowledge)
def update_knowledge(knowledge_id: UUID, payload: KnowledgeUpdate, db: DbSession) -> KnowledgeTable:
    row = _get_or_404(db, knowledge_id)
    changes = payload.model_dump(exclude_unset=True)
    cbr_changed = False
    for key, value in changes.items():
        if key == "status":
            row.status = value.value if hasattr(value, "value") else value
            continue
        setattr(row, key, value.value if hasattr(value, "value") else value)
        if key in _CBR_KEYS:
            cbr_changed = True
    if cbr_changed:
        _refresh_search_fields(row)
    db.commit()
    return row


@router.delete("/{knowledge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(knowledge_id: UUID, db: DbSession) -> None:
    row = _get_or_404(db, knowledge_id)
    row.deleted_at = func.now()
    db.commit()
