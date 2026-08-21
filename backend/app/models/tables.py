"""SQLAlchemyのテーブル定義。

**DDLの正は `docker/initdb/02_schema.sql`。** ここはクエリを書くための対応定義。
`Base.metadata.create_all()` は使わない。DDLが2箇所にあると必ず食い違い、
どちらが正しいか分からなくなるため。

スキーマを変更するときは、
  1. `docker/initdb/02_schema.sql` を直す（DDLの正）
  2. このファイルを合わせる
  3. `knowledge.py` のPydanticモデルを合わせる（APIとLLMに出る型）
  4. `docker compose down -v && docker compose up -d` で作り直す
  5. `/health/db` で `embedding_dim_matches` を確認する
の順で行う（CLAUDE.md 6章 / 3.1 / 4.10）。**3つを揃えて直すこと。**

ベクトル列の次元数は config.py の DEFAULT_EMBEDDING_DIM を参照している。
SQL側の vector(N) と食い違うと挿入時にエラーになるため、
`/health/db` で実際のDBの次元数を突き合わせて検知できるようにしてある。
"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, FetchedValue, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.db import Base

_EMBEDDING_DIM = get_settings().embedding_dim


class KnowledgeTable(Base):
    __tablename__ = "knowledge"

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'confirmed', 'rejected')",
            name="ck_knowledge_status",
        ),
        CheckConstraint(
            "source_type IN ('manual', 'meeting', 'audio')",
            name="ck_knowledge_source_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    original_content: Mapped[str | None] = mapped_column(Text)

    # 登録後に非同期で生成することがあるためNULLを許容する
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM))

    status: Mapped[str] = mapped_column(String, nullable=False, server_default="confirmed")
    source_type: Mapped[str] = mapped_column(String, nullable=False, server_default="manual")
    source_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_by: Mapped[str | None] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # updated_at はDB側のトリガーが更新する。
    # server_onupdate を書かないと SQLAlchemy がその事実を知らず、
    # UPDATE後もオブジェクトが古い日時を保持し続ける
    # （db.py が expire_on_commit=False なので commit しても再取得されない）。
    # FetchedValue() を指定すると更新後の値を取り直してくれる。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=FetchedValue(),
    )
    # 論理削除。物理削除しないのは誤削除から復帰できるようにするため
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
