"""ナレッジのCRUD。

入力時に構造化を強制しない（実装計画 §4）。
必須は content だけで、分類や整理は後から行う。

埋め込みはこのエンドポイント内で同期的に生成する。
1件あたり0.1秒程度で、音声処理のように非同期にする必要がないため
（CLAUDE.md 6章の非同期化は音声を対象にした原則）。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.knowledge import (
    Knowledge,
    KnowledgeCreate,
    KnowledgeStatus,
    KnowledgeUpdate,
)
from app.models.tables import KnowledgeTable
from app.services.embedding import embed_passages

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DbSession = Annotated[Session, Depends(get_db)]


def _get_or_404(db: Session, knowledge_id: UUID) -> KnowledgeTable:
    """論理削除済みのものは存在しない扱いにする。"""
    row = db.execute(
        select(KnowledgeTable).where(
            KnowledgeTable.id == knowledge_id,
            KnowledgeTable.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge が見つかりません")
    return row


@router.post("", response_model=Knowledge, status_code=status.HTTP_201_CREATED)
def create_knowledge(payload: KnowledgeCreate, db: DbSession) -> KnowledgeTable:
    """ナレッジを登録し、同時に埋め込みを生成する。"""
    row = KnowledgeTable(**payload.model_dump())
    row.embedding = embed_passages([payload.content])[0]
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
    """新しい順に一覧を返す。"""
    stmt = select(KnowledgeTable).where(KnowledgeTable.deleted_at.is_(None))
    if status_filter is not None:
        stmt = stmt.where(KnowledgeTable.status == status_filter)
    stmt = stmt.order_by(KnowledgeTable.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


@router.get("/count")
def count_knowledge(db: DbSession) -> dict[str, int]:
    """状態ごとの件数。一覧のページングと、蓄積量の把握に使う。"""
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


@router.patch("/{knowledge_id}", response_model=Knowledge)
def update_knowledge(knowledge_id: UUID, payload: KnowledgeUpdate, db: DbSession) -> KnowledgeTable:
    """指定した項目だけ更新する。

    content を変えたら埋め込みを作り直す。
    本文と埋め込みがズレると、検索で別の内容がヒットするようになるため。
    """
    row = _get_or_404(db, knowledge_id)
    changes = payload.model_dump(exclude_unset=True)

    if "content" in changes:
        row.content = changes["content"]
        row.embedding = embed_passages([row.content])[0]
    if "status" in changes:
        row.status = changes["status"]

    db.commit()
    return row


@router.delete("/{knowledge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(knowledge_id: UUID, db: DbSession) -> None:
    """論理削除。物理削除しないのは誤削除から復帰できるようにするため。"""
    row = _get_or_404(db, knowledge_id)
    row.deleted_at = func.now()
    db.commit()
