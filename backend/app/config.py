"""アプリ設定。

環境ごとに異なる値（DB接続先、DGXのIPなど）をコード中に散らかさず、
リポジトリ直下の .env に集約するために存在する。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env はリポジトリ直下に置く。backend/ 配下ではないので2階層上を見る
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# 埋め込みモデルは確定済み（docs/decisions.md 参照）。
# DGX上のvLLMは固定モデルを配信しており埋め込みを載せられないため、
# 埋め込みは各自のPCのCPUで実行する。
#
# DEFAULT_EMBEDDING_DIM は docker/initdb/02_schema.sql の vector(N) と
# 必ず一致させること。片方だけ変えると、挿入時までエラーに気づけない。
DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_EMBEDDING_DIM = 1024

_EMBEDDING_DEFAULTS: dict[str, object] = {
    "embedding_model": DEFAULT_EMBEDDING_MODEL,
    "embedding_dim": DEFAULT_EMBEDDING_DIM,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 既定は5433。docker-compose.yml と揃えること（5432はネイティブPostgreSQLと衝突する）
    database_url: str = "postgresql+psycopg://torano:torano_dev_password@localhost:5433/torano_maki"

    # LLM推論サーバ（DGX Spark 上の vLLM）。OpenAI互換なので末尾に /v1 を含める。
    # 環境変数名を BASE_URL / MODEL_NAME としているのは、
    # 推論サーバを差し替えても名前が実態と食い違わないようにするため。
    base_url: str = ""
    model_name: str = ""

    # 埋め込みは確定済み。DBの vector(N) と embedding_dim は必ず一致させること。
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = DEFAULT_EMBEDDING_DIM

    @field_validator("embedding_model", "embedding_dim", mode="before")
    @classmethod
    def _blank_to_default(cls, v: object, info: ValidationInfo) -> object:
        """空文字は「未指定」とみなして既定値に倒す。

        古い .env（EMBEDDING_DIM= が空のまま）を持っている人がいるため。
        空文字は int にパースできず、起動した瞬間に ValidationError で落ちて
        原因が分かりにくい。既定値に倒せば、.env を更新していない人でも動く。

        なお before バリデータが None を返しても pydantic は既定値に
        フォールバックしない（環境変数が存在する時点で「指定あり」と扱われる）。
        そのため既定値を明示的に返す必要がある。
        """
        if isinstance(v, str) and not v.strip():
            return _EMBEDDING_DEFAULTS[info.field_name]
        return v

    @property
    def is_llm_configured(self) -> bool:
        return bool(self.base_url) and bool(self.model_name)

    @property
    def is_embedding_configured(self) -> bool:
        return bool(self.embedding_model) and bool(self.embedding_dim)


@lru_cache
def get_settings() -> Settings:
    """設定の読み込みは1回で足りるためキャッシュする。"""
    return Settings()
