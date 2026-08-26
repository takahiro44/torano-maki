"""DGX Spark 上の vLLM（OpenAI互換）への呼び出し口。

**なぜ切り出すか。**
vLLM の呼び出しは `extraction.py` の中にあるが、あれは
「JSON Schema を渡して構造化ナレッジを1回で取り出す」専用の作りで、
Tool Calling を伴う複数往復には使えない。
チャット側から再利用できる形が無かったため、汎用の呼び出しをここに置く。

将来 `extraction.py` もこちらへ寄せられるが、担当領域が違うため
今回は触らない（CLAUDE.md 4.5）。

**接続先が落ちていることを前提にする。** DGXは貸し出し機で、
こちらでサーバを選べない。到達できない場合は握り潰さず、
呼び出し側が 502 / 503 に振り分けられる形で送出する。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

# 例外は extraction.py が先に定義したものをそのまま使う。
# 同名のクラスを2つ作ると、except の取り違えでエラーを握り潰す事故が起きる。
# 本来は両方こちらへ寄せたいが、extraction は担当領域が違うため触らない。
from app.services.extraction import LlmNotConfiguredError, LlmRequestError

logger = logging.getLogger(__name__)

__all__ = ["LlmNotConfiguredError", "LlmRequestError", "chat_completion"]

# 27B モデルで Tool Calling を挟むと1往復でも数秒〜十数秒かかる。
# 既定の 5 秒などにすると、正常な応答をタイムアウトとして捨ててしまう。
DEFAULT_TIMEOUT_SECONDS = 120.0


def chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.3,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    json_schema: dict[str, Any] | None = None,
    schema_name: str = "structured_output",
) -> dict[str, Any]:
    """`/chat/completions` を1回呼び、応答のJSONをそのまま返す。

    パースして畳まずに生の body を返すのは、Tool Calling では
    `tool_calls` / `finish_reason` / `usage` を呼び出し側が同時に見るため。
    ここで型を決め打つと、Agent Loop 側が必要な情報を取り出せなくなる。

    `json_schema` を渡すと構造化出力（guided decoding）を要求する。
    **Tool Calling とは併用しない。** Tool を呼ぶかどうかをモデルが選ぶ場面と、
    決まった形のJSONを1回で受け取る場面は別物で、混ぜると
    「Tool を呼びたいのにスキーマへ押し込められる」状態になる。
    キー名は extraction.py が実績のある形（`response_format` と
    `structured_outputs` の併記）に合わせている。vLLM のバージョンにより
    受け付ける側が違うため、両方送るのが確実だった。
    """
    settings = get_settings()
    if not settings.is_llm_configured:
        raise LlmNotConfiguredError("BASE_URL / MODEL_NAME が未設定。.env を確認すること")

    payload: dict[str, Any] = {
        "model": settings.model_name,
        "messages": messages,
        "temperature": temperature,
        # Qwen の思考モードを切る。デモでは応答速度を優先する。
        # 思考を有効にすると Tool 選択の質は上がりうるが、
        # 1往復あたりの待ち時間が数倍になり、複数往復では体感が破綻する。
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    elif json_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": json_schema},
        }
        payload["structured_outputs"] = {"json": json_schema}

    url = settings.base_url.rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        logger.exception("vLLM へのチャットリクエストに失敗しました")
        raise LlmRequestError(f"vLLM に接続できません: {exc}") from exc
