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

# --- ハイブリッド検索のパラメータ ---
#
# ここに集約しているのは、検索の効き方を変える唯一のつまみだから。
# services/search.py に直接書くと、調整するたびにコードを触ることになり、
# 「誰がどの値で評価したのか」が追えなくなる。
#
# 現在のナレッジは十数件規模のため Top-K は広めに取ってある。
# 件数が増えたら tests/test_search.py の比較評価を見ながら絞ること。
DEFAULT_SEMANTIC_TOP_K = 20
DEFAULT_LEXICAL_TOP_K = 20
DEFAULT_HYBRID_TOP_K = 5

# RRF の順位補正定数。大きいほど上位と下位の差が縮まる。
# 60 は Cormack et al. (2009) が提示した値で、広く既定として使われている。
DEFAULT_RRF_K = 60

# pg_trgm の word_similarity の足切り。
#
# **semantic のスコアと違い、こちらは閾値が機能する。**
# e5 の類似度は無関係でも 0.78 程度出るため絶対値で判定できないが、
# trgm は無関係なら 0.0 になる（「今日の天気」で全件 0.0 を実測）。
#
# 0.4 はシード16件での実測から決めた。スコアには明確な溝がある。
#
#   クエリ全体が一致  0.529〜0.857（2位との差も大きい）
#   ------------------ 0.4 ------------------
#   一部だけが一致    0.385以下（2位との差が小さく、順位が実質ランダム）
#
# 下側は「A社の導入予定」で『A社の』だけが一致しているような状態で、
# どのA社ナレッジが1位になるかはほぼ運になる。これを RRF に流すと
# semantic が正しく出した1位を押し出してしまう（実際に回帰を起こした）。
# **確信が持てないときは lexical 側が何も返さない**方が良い結果になる。
#
# 下げると語彙検索が積極的になるが、長文クエリでノイズが増える。
# 変更したら tests/test_search.py の比較評価で劣化を確認すること。
DEFAULT_LEXICAL_MIN_SIMILARITY = 0.4


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

    # 音声認識サーバ（DGX Spark 上の faster-whisper）。
    # STT_BASE_URL は OpenAI 互換の /v1/audio/transcriptions まで含める。
    stt_base_url: str = ""
    stt_model: str = "medium"

    # 埋め込みは確定済み。DBの vector(N) と embedding_dim は必ず一致させること。
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = DEFAULT_EMBEDDING_DIM

    # ハイブリッド検索。既定値の根拠はこのファイル冒頭の定数を参照
    semantic_top_k: int = DEFAULT_SEMANTIC_TOP_K
    lexical_top_k: int = DEFAULT_LEXICAL_TOP_K
    hybrid_top_k: int = DEFAULT_HYBRID_TOP_K
    rrf_k: int = DEFAULT_RRF_K
    lexical_min_similarity: float = DEFAULT_LEXICAL_MIN_SIMILARITY

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
    def is_stt_configured(self) -> bool:
        return bool(self.stt_base_url) and bool(self.stt_model)

    @property
    def is_embedding_configured(self) -> bool:
        return bool(self.embedding_model) and bool(self.embedding_dim)


@lru_cache
def get_settings() -> Settings:
    """設定の読み込みは1回で足りるためキャッシュする。"""
    return Settings()
