"""SQLAlchemyのテーブル定義。

**DDLの正は `docker/initdb/02_schema.sql`。** ここはクエリ用の対応定義。
`Base.metadata.create_all()` は使わない。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    FetchedValue,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    file_name: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    segments: Mapped[list[UtteranceSegmentTable]] = relationship(back_populates="data_source")
    knowledge_items: Mapped[list[KnowledgeUnitTable]] = relationship(back_populates="data_source")
    summary: Mapped[CallSummaryTable | None] = relationship(
        back_populates="data_source", uselist=False
    )


class UtteranceSegmentTable(Base):
    __tablename__ = "utterance_segments"

    __table_args__ = (
        UniqueConstraint("data_source_id", "sequence_no", name="uq_segment_sequence"),
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    data_source_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(100), nullable=False)
    start_sec: Mapped[float] = mapped_column(Float, nullable=False)
    end_sec: Mapped[float] = mapped_column(Float, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    data_source: Mapped[DataSourceTable] = relationship(back_populates="segments")


class KnowledgeUnitTable(Base):
    __tablename__ = "knowledge_units"

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
    problem: Mapped[str | None] = mapped_column(Text)
    judgment: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    lesson: Mapped[str | None] = mapped_column(Text)

    applicable_situations: Mapped[str | None] = mapped_column(Text)
    limitations: Mapped[str | None] = mapped_column(Text)

    industry: Mapped[str | None] = mapped_column(String(100))
    product: Mapped[str | None] = mapped_column(String(100))
    sales_stage: Mapped[str | None] = mapped_column(String(50))

    search_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(100))

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
    evidence: Mapped[list[KnowledgeEvidenceTable]] = relationship(
        back_populates="knowledge", cascade="all, delete-orphan"
    )


class KnowledgeEvidenceTable(Base):
    __tablename__ = "knowledge_evidence"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    knowledge_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_units.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_utterance_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("utterance_segments.id"), nullable=False
    )
    end_utterance_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("utterance_segments.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    knowledge: Mapped[KnowledgeUnitTable] = relationship(back_populates="evidence")
    start_utterance: Mapped[UtteranceSegmentTable] = relationship(foreign_keys=[start_utterance_id])
    end_utterance: Mapped[UtteranceSegmentTable] = relationship(foreign_keys=[end_utterance_id])


class CallSummaryTable(Base):
    __tablename__ = "call_summaries"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    data_source_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False, unique=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    customer_needs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    proposals: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    decisions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    next_actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    data_source: Mapped[DataSourceTable] = relationship(back_populates="summary")


class ChatReviewTable(Base):
    __tablename__ = "chat_reviews"

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'answered')", name="ck_chat_reviews_status"),
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    chat_history: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    understood_points: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    knowledge_gaps: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    supervisor_response: Mapped[str | None] = mapped_column(Text)
    answered_data_source_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("data_sources.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# 既存の検索・CRUD が KnowledgeTable 名を参照していたため。
KnowledgeTable = KnowledgeUnitTable
