"""疎通確認用のエンドポイント。

環境構築が正しく完了したかを、4人がそれぞれ自力で確認できるようにするために置く。
「動かない」の相談が来たとき、まずここを叩いてもらえば
アプリ側の問題かDB側の問題かを切り分けられる。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db

router = APIRouter(prefix="/health", tags=["health"])

# 依存性は Annotated で書く。
# 引数のデフォルト値に Depends() を直接書く書き方は ruff の B008 に引っかかるうえ、
# 型が読みにくくなるため、プロジェクト全体でこちらに統一する。
DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ExtensionInfo(BaseModel):
    name: str
    version: str


class DbHealthResponse(BaseModel):
    status: Literal["ok", "error"]
    postgres_version: str | None = None
    extensions: list[ExtensionInfo] = []
    detail: str | None = None


class ConfigHealthResponse(BaseModel):
    """未設定の項目を可視化する。着手前に何が足りないかを示すため。"""

    embedding_configured: bool
    embedding_model: str | None
    embedding_dim: int | None
    llm_configured: bool
    base_url: str | None
    model_name: str | None


@router.get("", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/db", response_model=DbHealthResponse)
def health_db(db: DbSession) -> DbHealthResponse:
    """DBに実際につながるか、pgvectorが有効かまで確認する。"""
    try:
        version = db.execute(text("SELECT current_setting('server_version')")).scalar_one()
        rows = db.execute(
            text(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname IN ('vector', 'pg_trgm') ORDER BY extname"
            )
        ).all()
        return DbHealthResponse(
            status="ok",
            postgres_version=str(version),
            extensions=[ExtensionInfo(name=r[0], version=r[1]) for r in rows],
        )
    except Exception as e:  # 接続失敗の理由をそのまま返す方が切り分けが速い
        return DbHealthResponse(status="error", detail=f"{type(e).__name__}: {e}")


@router.get("/config", response_model=ConfigHealthResponse)
def health_config(settings: AppSettings) -> ConfigHealthResponse:
    return ConfigHealthResponse(
        embedding_configured=settings.is_embedding_configured,
        embedding_model=settings.embedding_model or None,
        embedding_dim=settings.embedding_dim,
        llm_configured=settings.is_llm_configured,
        base_url=settings.base_url or None,
        model_name=settings.model_name or None,
    )
