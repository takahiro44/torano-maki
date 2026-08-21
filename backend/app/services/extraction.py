"""LLMによる構造化ナレッジの抽出。

推論サーバは DGX Spark 上の vLLM（OpenAI互換API）。

⚠️ 2026-08-21 時点で、**このサーバは JSON Schema による出力制約が効かない。**
`response_format: json_schema` も vLLM 固有の `guided_json` も無視され、
指定していないキー名やコードフェンス付きの応答が返ってくる。
そのため「Pydanticの JSON Schema をそのまま渡せば構造化される」前提には立てない。
実装方針は docs/decisions.md の決定に従うこと。
"""

from app.config import get_settings


def extract_knowledge(text: str) -> object:
    """商談テキストから構造化ナレッジを生成する。

    実装は未着手。models/knowledge.py のスキーマ確定後に着手する。
    """
    settings = get_settings()
    if not settings.is_llm_configured:
        raise RuntimeError("BASE_URL / MODEL_NAME が未設定。.env を確認すること")
    raise NotImplementedError("ナレッジスキーマの確定後に実装する")
