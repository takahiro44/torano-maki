"""埋め込み生成。

モデルの読み込みは重いため、プロセス内で1回だけ行う。
また埋め込みの次元数はDBの vector(N) と一致している必要があり、
ズレると挿入時まで気づけないため、ここで明示的に検証する。
"""

from app.config import get_settings


def embed(texts: list[str]) -> list[list[float]]:
    """テキストをベクトル化する。

    実装は未確定。sentence-transformers を使うか、Ollama に任せるかは
    docs/decisions.md で決める。
    """
    settings = get_settings()
    if not settings.is_embedding_configured:
        raise RuntimeError(
            "EMBEDDING_MODEL / EMBEDDING_DIM が未設定。"
            ".env を確認し、docs/decisions.md の決定に従って設定すること"
        )
    raise NotImplementedError("埋め込みの実装が未着手")
