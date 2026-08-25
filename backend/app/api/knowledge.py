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
    KnowledgeSortField,
    KnowledgeStatus,
    KnowledgeStatusPatch,
    KnowledgeUpdate,
    SortDirection,
)
from app.models.tables import DataSourceTable, KnowledgeUnitTable
from app.services.embedding import generate_embedding
from app.services.extraction import (
    LlmNotConfiguredError,
    LlmRequestError,
    process_text_to_knowledge,
)
from app.services.search_text import generate_search_text_from_mapping

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DbSession = Annotated[Session, Depends(get_db)]

_SEARCH_KEYS = (
    "title",
    "situation",
    "problem",
    "judgment",
    "action",
    "reasoning",
    "outcome",
    "lesson",
    "applicable_situations",
    "limitations",
    "industry",
    "product",
    "sales_stage",
)

_SORT_COLUMNS = {
    KnowledgeSortField.CREATED_AT: KnowledgeUnitTable.created_at,
    KnowledgeSortField.UPDATED_AT: KnowledgeUnitTable.updated_at,
    KnowledgeSortField.TITLE: KnowledgeUnitTable.title,
    KnowledgeSortField.STATUS: KnowledgeUnitTable.status,
}


def _get_or_404(db: Session, knowledge_id: UUID) -> KnowledgeUnitTable:
    row = db.execute(
        select(KnowledgeUnitTable).where(
            KnowledgeUnitTable.id == knowledge_id,
            KnowledgeUnitTable.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge が見つかりません")
    return row


def _refresh_search_fields(row: KnowledgeUnitTable) -> None:
    from app.config import get_settings

    row.search_text = generate_search_text_from_mapping(
        {key: getattr(row, key) for key in _SEARCH_KEYS}
    )
    row.embedding = generate_embedding(row.search_text)
    row.embedding_model = get_settings().embedding_model


@router.post("/extract", response_model=list[Knowledge])
def extract_and_store(payload: ExtractRequest, db: DbSession) -> list[KnowledgeUnitTable]:
    """テキストからナレッジを抽出し draft で格納する。"""
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
def create_knowledge(payload: KnowledgeCreate, db: DbSession) -> KnowledgeUnitTable:
    data_source_id = payload.data_source_id
    if data_source_id is None:
        source = DataSourceTable(source_type="manual")
        db.add(source)
        db.flush()
        data_source_id = source.id

    dump = payload.model_dump()
    dump["data_source_id"] = data_source_id
    dump["status"] = payload.status.value if hasattr(payload.status, "value") else payload.status
    row = KnowledgeUnitTable(**dump)
    _refresh_search_fields(row)
    db.add(row)
    db.commit()
    return row


@router.get("", response_model=list[Knowledge])
def list_knowledge(
    db: DbSession,
    status_filter: Annotated[KnowledgeStatus | None, Query(alias="status")] = None,
    industry: Annotated[str | None, Query()] = None,
    product: Annotated[str | None, Query()] = None,
    sales_stage: Annotated[str | None, Query()] = None,
    knowledge_type: Annotated[str | None, Query()] = None,
    sort: Annotated[KnowledgeSortField, Query()] = KnowledgeSortField.CREATED_AT,
    order: Annotated[SortDirection, Query()] = SortDirection.DESC,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[KnowledgeUnitTable]:
    stmt = select(KnowledgeUnitTable).where(KnowledgeUnitTable.deleted_at.is_(None))
    if status_filter is not None:
        stmt = stmt.where(KnowledgeUnitTable.status == status_filter)
    if industry is not None:
        stmt = stmt.where(KnowledgeUnitTable.industry == industry)
    if product is not None:
        stmt = stmt.where(KnowledgeUnitTable.product == product)
    if sales_stage is not None:
        stmt = stmt.where(KnowledgeUnitTable.sales_stage == sales_stage)
    if knowledge_type is not None:
        stmt = stmt.where(KnowledgeUnitTable.knowledge_type == knowledge_type)
    column = _SORT_COLUMNS[sort]
    ordered = column.asc() if order == SortDirection.ASC else column.desc()
    stmt = stmt.order_by(ordered, KnowledgeUnitTable.id.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


@router.get("/count")
def count_knowledge(db: DbSession) -> dict[str, int]:
    rows = db.execute(
        select(KnowledgeUnitTable.status, func.count())
        .where(KnowledgeUnitTable.deleted_at.is_(None))
        .group_by(KnowledgeUnitTable.status)
    ).all()
    counts = {s.value: 0 for s in KnowledgeStatus}
    for name, n in rows:
        counts[name] = n
    counts["total"] = sum(counts.values())
    return counts


@router.get("/{knowledge_id}", response_model=Knowledge)
def get_knowledge(knowledge_id: UUID, db: DbSession) -> KnowledgeUnitTable:
    return _get_or_404(db, knowledge_id)


@router.patch("/{knowledge_id}/status", response_model=Knowledge)
def patch_knowledge_status(
    knowledge_id: UUID, payload: KnowledgeStatusPatch, db: DbSession
) -> KnowledgeUnitTable:
    row = _get_or_404(db, knowledge_id)
    row.status = payload.status.value
    db.commit()
    return row


@router.patch("/{knowledge_id}", response_model=Knowledge)
def update_knowledge(
    knowledge_id: UUID, payload: KnowledgeUpdate, db: DbSession
) -> KnowledgeUnitTable:
    row = _get_or_404(db, knowledge_id)
    changes = payload.model_dump(exclude_unset=True)
    search_changed = False
    for key, value in changes.items():
        if key == "status":
            row.status = value.value if hasattr(value, "value") else value
            continue
        setattr(row, key, value.value if hasattr(value, "value") else value)
        if key in _SEARCH_KEYS:
            search_changed = True
    if search_changed:
        _refresh_search_fields(row)
    db.commit()
    return row


@router.delete("/{knowledge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(knowledge_id: UUID, db: DbSession) -> None:
    row = _get_or_404(db, knowledge_id)
    row.deleted_at = func.now()
    db.commit()
