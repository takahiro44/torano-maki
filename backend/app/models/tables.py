"""SQLAlchemyのテーブル定義。

**DDLの正は `docker/initdb/02_schema.sql`。** ここはクエリ用の対応定義。
`Base.metadata.create_all()` は使わない。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, FetchedValue, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base

_EMBEDDING_DIM = get_settings().embedding_dim


class DataSourceTable(Base):
    __tablename__ = "data_sources"

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('audio', 'document', 'manual', 'roleplay', 'interview')",
            name="ck_data_sources_source_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255))
    conducted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    knowledge_items: Mapped[list[KnowledgeTable]] = relationship(back_populates="data_source")


class KnowledgeTable(Base):
    __tablename__ = "knowledge"

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'confirmed', 'rejected', 'archived')",
            name="ck_knowledge_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    data_source_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("data_sources.id")
    )

    knowledge_type: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="sales_knowhow"
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)

    situation: Mapped[str | None] = mapped_column(Text)
    customer_issue: Mapped[str | None] = mapped_column(Text)
    sales_action: Mapped[str | None] = mapped_column(Text)
    action_reason: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    learning: Mapped[str | None] = mapped_column(Text)

    search_text: Mapped[str | None] = mapped_column(Text)
    original_content: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM))

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=FetchedValue(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    data_source: Mapped[DataSourceTable | None] = relationship(back_populates="knowledge_items")
