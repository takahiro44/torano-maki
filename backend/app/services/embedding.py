"""埋め込み生成。

DGX上のvLLMは固定モデルを配信しており `/v1/embeddings` を持たないため、
埋め込みは各自のPCのCPUで実行する（docs/decisions.md 参照）。

このモジュールが存在する理由は3つ。

1. **モデルの読み込みを1回に抑える。**
   毎回読み込むと検索のたびに数秒待たされる。

2. **プレフィックスの付け忘れを防ぐ。**
   `e5` 系のモデルは、保存する文章には `passage: `、検索クエリには `query: `
   を付ける前提で学習されている。**付け忘れてもエラーにならず精度だけ落ちる**
   ため事故に気づけない。そこで用途ごとに関数を分け、呼び出し側が
   プレフィックスを意識しなくてよい形にしている。
   利用側は SentenceTransformer を直接触らないこと。

3. **次元数のズレを読み込み時に検知する。**
   DBの `vector(N)` と食い違うと、挿入する瞬間まで誰も気づけない。
"""

import logging
import threading
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# e5系モデルが要求するプレフィックス。モデルを変えるときはここも見直すこと
_PASSAGE_PREFIX = "passage: "
_QUERY_PREFIX = "query: "

# 読み込み済みモデル。FastAPIは同期エンドポイントをスレッドプールで動かすため、
# 複数スレッドが同時に読み込もうとしないようロックで保護する
_model: "SentenceTransformer | None" = None
_model_lock = threading.Lock()


def _get_model() -> "SentenceTransformer":
    """モデルを1度だけ読み込む。

    初回は約2.2GBのダウンロードが走り、数分かかることがある。
    そのため import 時ではなく実際に使うときまで遅延させている
    （torch の import 自体が重く、uvicorn の起動が遅くなるため）。
    """
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        # ロック待ちの間に他スレッドが読み込みを終えている場合がある
        if _model is not None:
            return _model

        from sentence_transformers import SentenceTransformer

        settings = get_settings()
        logger.info(
            "埋め込みモデルを読み込みます: %s（初回はダウンロードに数分かかります）",
            settings.embedding_model,
        )
        model = SentenceTransformer(settings.embedding_model)

        # sentence-transformers 6.0 で get_sentence_embedding_dimension から改名された
        actual_dim = model.get_embedding_dimension()
        if actual_dim != settings.embedding_dim:
            raise RuntimeError(
                f"埋め込みの次元数が食い違っています。"
                f"モデル {settings.embedding_model} は {actual_dim} 次元ですが、"
                f"設定は {settings.embedding_dim} 次元です。"
                f" docker/initdb/02_schema.sql の vector(N)、"
                f"backend/app/config.py の DEFAULT_EMBEDDING_DIM、"
                f".env の EMBEDDING_DIM の3箇所を揃えたうえで、"
                f"docker compose down -v && docker compose up -d でDBを作り直してください。"
            )

        logger.info("埋め込みモデルを読み込みました（%d次元）", actual_dim)
        _model = model
        return _model


def _encode(texts: list[str]) -> list[list[float]]:
    """正規化済みのベクトルを返す。

    正規化しておくとコサイン距離が安定する。DB側も pgvector の `<=>`
    （コサイン距離）で検索する前提なので、ここを変えると
    インデックス（vector_cosine_ops）との整合が崩れる。
    """
    model = _get_model()
    # show_progress_bar は既定で有効。サーバのログに進捗バーが混ざって読みにくいため切る
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_passages(texts: list[str]) -> list[list[float]]:
    """蓄積するナレッジ本文をベクトル化する。DBに保存する側はこちらを使う。"""
    if not texts:
        return []
    return _encode([_PASSAGE_PREFIX + t for t in texts])


def embed_query(text: str) -> list[float]:
    """検索クエリをベクトル化する。検索する側はこちらを使う。

    `embed_passages` と取り違えても例外は出ないが、検索精度が落ちる。
    """
    return _encode([_QUERY_PREFIX + text])[0]
