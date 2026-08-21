"""テストの共通設定。

**開発中のDBを壊さないこと**を最優先にしている。
各テストをトランザクションで包み、必ずロールバックするため、
テストを流してもローカルのナレッジは消えない。

DBコンテナが起動している必要がある:
    docker compose up -d
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.db import engine, get_db
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _require_db() -> None:
    """DBに繋がらない場合、原因が分かる形で止める。"""
    try:
        with engine.connect():
            pass
    except Exception as e:  # 接続失敗の理由をそのまま見せたい
        pytest.exit(
            f"DBに接続できません: {e}\ndocker compose up -d でDBを起動してから実行してください。",
            returncode=1,
        )


@pytest.fixture
def db() -> Iterator[Session]:
    """テストごとにロールバックするセッション。

    外側のトランザクションを張り、テスト内の commit は
    そのトランザクション内のセーブポイントとして扱われる。
    最後に外側をロールバックすることで、書き込みは一切残らない。
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """アプリのDBセッションをテスト用に差し替えたクライアント。

    差し替えないとエンドポイントが本物のセッションを使ってしまい、
    書き込みがロールバックされずに残る。
    """

    def _get_db_override() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    # あえて with を使わない。コンテキストマネージャにすると lifespan が動き、
    # テストのたびに埋め込みモデルの事前読み込み（20秒以上）が走るため。
    # 埋め込みが要るテストでは遅延読み込みが働くので支障はない。
    yield TestClient(app)
    app.dependency_overrides.clear()
