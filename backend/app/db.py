"""DB接続。

セッションの作り方を1箇所に集約し、各エンドポイントが個別に
エンジンを作らないようにするために存在する。
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    # 接続が切れたまま使い回して落ちるのを防ぐ
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """SQLAlchemyモデルの基底クラス。テーブル定義は models/tables.py に置く。"""


def get_db() -> Generator[Session, None, None]:
    """FastAPIのDependsで使うセッション。処理後に必ず閉じるための仕組み。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
