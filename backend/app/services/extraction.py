"""LLMによる構造化ナレッジの抽出。

Pydanticモデルの JSON Schema をそのまま Ollama に渡すことで、
「スキーマ定義は1箇所」（CLAUDE.md 6章）を実現する。
プロンプトで「JSONで返して」と依頼する方式はパースが不安定なため使わない。
"""

from app.config import get_settings


def extract_knowledge(text: str) -> object:
    """商談テキストから構造化ナレッジを生成する。

    実装は未着手。models/knowledge.py のスキーマ確定後に着手する。
    """
    settings = get_settings()
    if not settings.ollama_model:
        raise RuntimeError("OLLAMA_MODEL が未設定。.env を確認すること")
    raise NotImplementedError("ナレッジスキーマの確定後に実装する")
