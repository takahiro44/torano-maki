"""アプリ設定。

環境ごとに異なる値（DB接続先、DGXのIPなど）をコード中に散らかさず、
リポジトリ直下の .env に集約するために存在する。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env はリポジトリ直下に置く。backend/ 配下ではないので2階層上を見る
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


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

    # 埋め込みモデルと次元数はまだ未確定（docs/decisions.md 参照）。
    # embedding_dim は DB の vector(N) と必ず一致させること。
    # 値が入っていない状態でベクトル列を作ろうとすると事故るため、
    # 利用側で is_embedding_configured を確認してから使う。
    embedding_model: str = ""
    embedding_dim: int | None = None

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
