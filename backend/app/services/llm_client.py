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

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx
from pydantic import BaseModel

from app.config import get_settings

# 例外は extraction.py が先に定義したものをそのまま使う。
# 同名のクラスを2つ作ると、except の取り違えでエラーを握り潰す事故が起きる。
# 本来は両方こちらへ寄せたいが、extraction は担当領域が違うため触らない。
from app.services.extraction import LlmNotConfiguredError, LlmRequestError

logger = logging.getLogger(__name__)

__all__ = [
    "LlmNotConfiguredError",
    "LlmRequestError",
    "LlmStreamChunk",
    "chat_completion",
    "chat_completion_stream",
]

# SSE の1行は `data: {...}`、終端は `data: [DONE]`
_SSE_DATA_PREFIX = "data:"
_SSE_DONE = "[DONE]"

# 27B モデルで Tool Calling を挟むと1往復でも数秒〜十数秒かかる。
# 既定の 5 秒などにすると、正常な応答をタイムアウトとして捨ててしまう。
DEFAULT_TIMEOUT_SECONDS = 120.0


def chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.3,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """`/chat/completions` を1回呼び、応答のJSONをそのまま返す。

    パースして畳まずに生の body を返すのは、Tool Calling では
    `tool_calls` / `finish_reason` / `usage` を呼び出し側が同時に見るため。
    ここで型を決め打つと、Agent Loop 側が必要な情報を取り出せなくなる。
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

    url = settings.base_url.rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        logger.exception("vLLM へのチャットリクエストに失敗しました")
        raise LlmRequestError(f"vLLM に接続できません: {exc}") from exc


class LlmStreamChunk(BaseModel):
    """SSEの1チャンクから、呼び出し側が使う情報だけを取り出したもの。

    生のJSONを渡さないのは、`choices[0].delta.content` のような
    深い添字アクセスが呼び出し側に散らばると、形が少し違うだけで
    KeyError で落ちるため（vLLM は同じ形のチャンクを数百件送ってくる）。

    **`tool_calls` は「あるか」しか持たない。** 断片を跨いで組み立てるのは
    壊れやすく、Tool 呼び出しのラウンドは非ストリーミングで取り直すため。

    usage は最終チャンクにだけ入る（`stream_options.include_usage`）。
    それ以外は 0 なので、呼び出し側は素朴に加算してよい。
    """

    delta: str = ""
    has_tool_calls: bool = False
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


def chat_completion_stream(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Iterator[LlmStreamChunk]:
    """`/chat/completions` を `stream: true` で呼び、届いた順にチャンクを返す。

    **なぜ必要か。** 27Bモデルの decode は約20 tok/s しか出ず、
    400トークンの回答を待ち切らせると20秒無言になる。
    最初のトークンは1秒台で届くので、届いた分から流せば体感が変わる。

    途中で呼び出し側が読むのをやめた場合（利用者の中止・切断）は、
    ジェネレータが閉じられ、`with` を抜けて接続も閉じる。
    """
    settings = get_settings()
    if not settings.is_llm_configured:
        raise LlmNotConfiguredError("BASE_URL / MODEL_NAME が未設定。.env を確認すること")

    payload: dict[str, Any] = {
        "model": settings.model_name,
        "messages": messages,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False},
        "stream": True,
        # usage は既存 ChatUsage を埋めるために要る。
        # 付けないとストリーミングでは一切返ってこない
        "stream_options": {"include_usage": True},
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    url = settings.base_url.rstrip("/") + "/chat/completions"
    try:
        with (
            httpx.Client(timeout=timeout) as client,
            client.stream("POST", url, json=payload) as response,
        ):
            if response.status_code >= 400:
                # ストリームでは本文が未読のままなので、エラー内容を読んでから判定する
                response.read()
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith(_SSE_DATA_PREFIX):
                    continue
                data = line[len(_SSE_DATA_PREFIX) :].strip()
                if data == _SSE_DONE:
                    return
                chunk = _parse_chunk(data)
                if chunk is not None:
                    yield chunk
    except httpx.HTTPError as exc:
        logger.exception("vLLM へのストリーミングリクエストに失敗しました")
        raise LlmRequestError(f"vLLM に接続できません: {exc}") from exc


def _parse_chunk(data: str) -> LlmStreamChunk | None:
    """壊れた1行でストリーム全体を落とさない。

    途中で例外にすると、そこまでに届いた回答ごと捨てることになる。
    読めない行は捨てて次を待つ方が利用者にとってましなため。
    """
    try:
        body = json.loads(data)
    except json.JSONDecodeError:
        logger.warning("ストリームの行を解釈できませんでした: %r", data[:200])
        return None
    if not isinstance(body, dict):
        return None

    choices = body.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    content = delta.get("content")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}

    return LlmStreamChunk(
        delta=content if isinstance(content, str) else "",
        has_tool_calls=bool(delta.get("tool_calls")),
        finish_reason=choice.get("finish_reason"),
        prompt_tokens=_int_or_zero(usage.get("prompt_tokens")),
        completion_tokens=_int_or_zero(usage.get("completion_tokens")),
    )


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) else 0
