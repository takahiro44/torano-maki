"""SQLAlchemyのテーブル定義。

knowledge.py のPydanticモデルに対応させる。構造がズレると
「スキーマ定義は1箇所」（CLAUDE.md 6章）が崩れるので、
片方だけを変更しないこと。

ベクトル列の次元数は EMBEDDING_DIM と一致していなければならない。
ズレたまま起動すると挿入時まで気づけないため、
テーブル定義時に設定から取得する。
"""

# =============================================================================
# TODO: knowledge.py のスキーマ確定後にテーブルを定義する。
#
# ベクトル列は次のように、設定から次元数を受け取る形にすること:
#
#   from pgvector.sqlalchemy import Vector
#   from app.config import get_settings
#   from app.db import Base
#
#   _dim = get_settings().embedding_dim
#
#   class KnowledgeTable(Base):
#       __tablename__ = "knowledge"
#       id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
#       source_id: Mapped[UUID] = mapped_column(index=True)   # 出典。必須
#       embedding: Mapped[list[float]] = mapped_column(Vector(_dim))
#       ...
#
# 次元数が未確定のため、現時点では定義しない。
# docs/decisions.md の「未決定」を参照。
# =============================================================================
