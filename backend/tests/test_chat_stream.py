"""`POST /chat/stream` のSSE契約と、ストリーミング版 Agent の制御。

**フロントが頼るのはイベントの順序と `type` の形。** ここが崩れると
画面が無言のまま固まるため、順序と最後の `done` を固定する。

**vLLM はモックする。** DGXが落ちていてもCIを回せるようにするためと、
生成が非決定的で「途中で Tool を呼び直す」経路を狙って再現できないため。
確かめたいのはモデルの賢さではなく、届いたチャンクをこちらが
正しくイベントへ変換できているか。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.models.chat import ChatMessage, ChatRole
from app.services.agent_stream import MAX_ANSWER_TOKENS, TRUNCATED_SUFFIX, stream_agent_answer
from app.services.llm_client import (
    LlmNotConfiguredError,
    LlmRequestError,
    LlmStreamChunk,
    chat_completion_stream,
)

# httpx.Client はテスト中に差し替えるため、本物を先に捕まえておく
# （差し替えたあとに httpx.Client() を呼ぶと自分自身を再帰的に呼んでしまう）
_REAL_HTTPX_CLIENT = httpx.Client

_ASK = {"messages": [{"role": "user", "content": "在庫の食い違いにどう対応した？"}]}


def _tool_call_body(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def _answer_body(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def _search_ok(knowledge_id: str, title: str) -> dict[str, Any]:
    return {
        "ok": True,
        "tool": "search_knowledge",
        "result": {
            "query": "在庫",
            "count": 1,
            "items": [{"id": knowledge_id, "title": title, "data_source_id": None}],
        },
    }


def _stream(*deltas: str, finish_reason: str = "stop", has_tool_calls: bool = False):
    """chat_completion_stream の差し替え。呼ばれるたびに新しい generator を返す。"""

    def _factory(*_args: Any, **_kwargs: Any) -> Iterator[LlmStreamChunk]:
        if has_tool_calls:
            yield LlmStreamChunk(has_tool_calls=True)
            return
        for delta in deltas:
            yield LlmStreamChunk(delta=delta)
        yield LlmStreamChunk(finish_reason=finish_reason)
        # usage は最終チャンクにだけ来る（stream_options.include_usage）
        yield LlmStreamChunk(prompt_tokens=7000, completion_tokens=300)

    return _factory


def _events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")
    ]


@pytest.fixture
def ask() -> list[ChatMessage]:
    return [ChatMessage(role=ChatRole.USER, content="在庫の食い違いにどう対応した？")]


@pytest.fixture
def fake_db() -> MagicMock:
    """入力元を引けない Session の代わり（test_agent_loop.py と同じ理由）。"""
    db = MagicMock()
    db.get.return_value = None
    return db


# --- エンドポイント（SSEの形） ---


def test_イベントは検索から回答まで順に届きdoneで終わる(client: TestClient) -> None:
    knowledge_id = str(uuid4())
    with (
        patch(
            "app.services.agent_stream.chat_completion",
            return_value=_tool_call_body("search_knowledge", {"query": "在庫"}),
        ),
        patch(
            "app.services.agent_loop.execute_agent_tool",
            return_value=_search_ok(knowledge_id, "在庫情報の非同期による顧客対応リスク"),
        ),
        patch(
            "app.services.agent_stream.chat_completion_stream",
            side_effect=_stream("在庫の", "食い違いは"),
        ),
    ):
        res = client.post("/chat/stream", json=_ASK)

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = _events(res.text)
    assert [e["type"] for e in events] == [
        "tool_call",
        "tool_result",
        "citations",
        "text",
        "text",
        "done",
    ]

    assert events[0] == {
        "type": "tool_call",
        "step": 1,
        "tool": "search_knowledge",
        "label": "ナレッジを検索しています",
        # top_k はリクエストの既定値で上書きされる
        "arguments": {"query": "在庫", "top_k": 5},
    }
    assert events[1] == {
        "type": "tool_result",
        "step": 1,
        "tool": "search_knowledge",
        "ok": True,
        "summary": "ナレッジを検索しました（1件）",
        "error_code": None,
    }
    assert events[2]["citations"][0]["knowledge_id"] == knowledge_id
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "在庫の食い違いは"
    assert events[-1]["usage"]["hit_max_iterations"] is False
    assert events[-1]["usage"]["completion_tokens"] == 320


def test_llmへ到達できない場合はerrorイベントで流れる(client: TestClient) -> None:
    """接続確立後の失敗はステータスコードで表現できない。"""
    with patch("app.api.chat.stream_agent_answer", side_effect=LlmRequestError("接続できません")):
        res = client.post("/chat/stream", json=_ASK)

    assert res.status_code == 200
    assert _events(res.text) == [
        {"type": "error", "code": "llm_unreachable", "message": "接続できません"}
    ]


def test_llm未設定はerrorイベントで流れる(client: TestClient) -> None:
    with patch("app.api.chat.stream_agent_answer", side_effect=LlmNotConfiguredError("未設定")):
        res = client.post("/chat/stream", json=_ASK)

    assert _events(res.text)[0]["code"] == "llm_not_configured"


def test_想定外の例外もerrorイベントにして内部事情は出さない(client: TestClient) -> None:
    with patch("app.api.chat.stream_agent_answer", side_effect=RuntimeError("DBのパスワード")):
        res = client.post("/chat/stream", json=_ASK)

    event = _events(res.text)[0]
    assert event["code"] == "internal"
    assert "パスワード" not in event["message"]


def _long_stream(closed: list[str]):
    """終わらない回答。中止されるまで書き続ける。"""

    def _factory(*_args: Any, **_kwargs: Any) -> Iterator[LlmStreamChunk]:
        try:
            for _ in range(10_000):
                yield LlmStreamChunk(delta="あ")
        except GeneratorExit:
            closed.append("closed")
            raise

    return _factory


def test_途中で切断してもサーバ側は例外で落ちない(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """中止ボタンで切られる前提。ログをエラーで汚さないこと。"""
    with (
        caplog.at_level(logging.ERROR),
        patch(
            "app.services.agent_stream.chat_completion",
            return_value=_tool_call_body("search_knowledge", {"query": "在庫"}),
        ),
        patch(
            "app.services.agent_loop.execute_agent_tool",
            return_value=_search_ok(str(uuid4()), "在庫の非同期"),
        ),
        patch("app.services.agent_stream.chat_completion_stream", side_effect=_long_stream([])),
    ):
        with client.stream("POST", "/chat/stream", json=_ASK) as res:
            assert res.status_code == 200
            received = [line for line in res.iter_lines() if line.startswith("data: ")]
            # 1イベントだけ読んで切断する（TestClient は with を抜けると閉じる）
            assert received

    assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_読むのをやめたらvLLMへの接続を閉じる(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    """中止されたら生成も止める。放っておくとDGXが書き続ける。

    HTTP層ではなくジェネレータの後始末そのものを確かめる。
    切断がどの層で検知されるかはサーバ実装に依存するが、
    「閉じられたら下まで閉じる」ことはこちらの責任。
    """
    closed: list[str] = []
    with (
        patch(
            "app.services.agent_stream.chat_completion",
            return_value=_tool_call_body("search_knowledge", {"query": "在庫"}),
        ),
        patch(
            "app.services.agent_loop.execute_agent_tool",
            return_value=_search_ok(str(uuid4()), "在庫の非同期"),
        ),
        patch("app.services.agent_stream.chat_completion_stream", side_effect=_long_stream(closed)),
    ):
        events = stream_agent_answer(fake_db, ask)
        for event in events:
            if event.type == "text":
                break  # 利用者が中止した
        events.close()

    assert closed == ["closed"]


def test_履歴が空なら422(client: TestClient) -> None:
    """入力の検証は `/chat` と同じ。ストリームを開く前に弾く。"""
    res = client.post("/chat/stream", json={"messages": []})
    assert res.status_code == 422


# --- Agent（イベントの作り方） ---


def test_toolを使わない質問はまとめて1イベントで流す(
    fake_db: MagicMock, ask: list[ChatMessage]
) -> None:
    """挨拶は1往復目で答えが返る。全文が手元にあるので分割しない。"""
    with patch(
        "app.services.agent_stream.chat_completion", return_value=_answer_body("こんにちは")
    ):
        events = list(stream_agent_answer(fake_db, ask))

    assert [e.type for e in events] == ["text", "done"]
    assert events[0].delta == "こんにちは"


def test_ストリームにtool_callsが出たら捨てて取り直す(
    fake_db: MagicMock, ask: list[ChatMessage]
) -> None:
    """2往復目以降は最終回答を期待して流すが、外れることがある。

    そのときは断片から組み立てず、非ストリーミングで取り直す。
    """
    bodies = [
        _tool_call_body("search_knowledge", {"query": "在庫"}),
        _tool_call_body("get_call_summary", {"knowledge_id": str(uuid4())}),
    ]
    streams = [_stream(has_tool_calls=True), _stream("最終回答です")]

    with (
        patch("app.services.agent_stream.chat_completion", side_effect=bodies),
        patch(
            "app.services.agent_loop.execute_agent_tool",
            return_value=_search_ok(str(uuid4()), "在庫の非同期"),
        ),
        patch(
            "app.services.agent_stream.chat_completion_stream",
            side_effect=lambda *a, **kw: streams.pop(0)(*a, **kw),
        ),
    ):
        events = list(stream_agent_answer(fake_db, ask))

    assert [e.type for e in events] == [
        "tool_call",
        "tool_result",
        "citations",
        "tool_call",
        "tool_result",
        "text",
        "done",
    ]
    # 捨てたストリームのテキストは流さない
    assert events[-2].delta == "最終回答です"


def test_上限に達したら打ち切って必ず文章で返す(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    with (
        patch(
            "app.services.agent_stream.chat_completion",
            side_effect=lambda *a, **kw: _tool_call_body("search_knowledge", {"query": "在庫"}),
        ),
        patch(
            "app.services.agent_loop.execute_agent_tool",
            return_value=_search_ok(str(uuid4()), "在庫の非同期"),
        ),
        patch(
            "app.services.agent_stream.chat_completion_stream",
            side_effect=lambda *a, **kw: (
                _stream(has_tool_calls=True)(*a, **kw)
                if kw.get("tools")
                else _stream("ここまでで分かったこと")(*a, **kw)
            ),
        ),
    ):
        events = list(stream_agent_answer(fake_db, ask, max_iterations=2))

    assert events[-1].type == "done"
    assert events[-1].usage.hit_max_iterations is True
    assert events[-2].delta == "ここまでで分かったこと"


def test_max_tokensで切れたら断り書きを足す(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    """尻切れを「AIが言い淀んだ」と読ませないため。"""
    with (
        patch(
            "app.services.agent_stream.chat_completion",
            return_value=_tool_call_body("search_knowledge", {"query": "在庫"}),
        ),
        patch(
            "app.services.agent_loop.execute_agent_tool",
            return_value=_search_ok(str(uuid4()), "在庫の非同期"),
        ),
        patch(
            "app.services.agent_stream.chat_completion_stream",
            side_effect=_stream("長い回答", finish_reason="length"),
        ) as stream,
    ):
        events = list(stream_agent_answer(fake_db, ask))

    assert events[-2].delta == TRUNCATED_SUFFIX
    assert stream.call_args.kwargs["max_tokens"] == MAX_ANSWER_TOKENS


def test_前置きのあとにTool呼び出しが来たら流した本文を取り消す(
    fake_db: MagicMock, ask: list[ChatMessage]
) -> None:
    """**この取り消しが無いと、前置きが最終回答として確定する。**

    モデルは「根拠の発言を確認します」と書いてから `tool_calls` を出すことがある。
    以前は本文が流れ始めたあとの `tool_calls` を無視していたため、
    モデルが要求した Tool を捨てて前置きだけを回答にしていた（実機で再現）。
    """

    def _preamble_then_tool_calls(*_args: Any, **_kwargs: Any) -> Iterator[LlmStreamChunk]:
        yield LlmStreamChunk(delta="根拠の発言を確認します。")
        yield LlmStreamChunk(has_tool_calls=True)

    with (
        patch(
            "app.services.agent_stream.chat_completion",
            side_effect=[
                _tool_call_body("search_knowledge", {"query": "在庫"}),
                _answer_body("調べ終えたあとの本当の回答"),
            ],
        ),
        patch(
            "app.services.agent_loop.execute_agent_tool",
            return_value=_search_ok(str(uuid4()), "在庫の非同期"),
        ),
        patch(
            "app.services.agent_stream.chat_completion_stream",
            side_effect=_preamble_then_tool_calls,
        ),
    ):
        events = list(stream_agent_answer(fake_db, ask))

    types = [e.type for e in events]
    assert "answer_reset" in types
    # 前置き → 取り消し → 本当の回答、の順でなければ画面に前置きが残る
    assert types.index("answer_reset") > types.index("text")
    assert events[-2].delta == "調べ終えたあとの本当の回答"


def test_回答が空なら_Toolを外して聞き直す(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    """**Tool も本文も返さないラウンドが実機で起きる。**

    そこで諦めると、調べ終えた Tool の結果ごと捨てて
    「回答を生成できませんでした」だけが残る。実際にその状態が出たため、
    Tool を外して必ず文章で答えさせる1回を挟むようにした。
    """
    with (
        patch("app.services.agent_stream.chat_completion", return_value=_answer_body("")),
        patch(
            "app.services.agent_stream.chat_completion_stream",
            side_effect=_stream("聞き直した回答"),
        ) as stream,
    ):
        events = list(stream_agent_answer(fake_db, ask))

    assert [e.type for e in events] == ["text", "done"]
    assert events[0].delta == "聞き直した回答"
    # 聞き直しでは Tool を外す。付けたままだとまた Tool を呼びに行き、終われない
    assert stream.call_args.kwargs["tools"] is None


def test_聞き直しても空なら文言を返す(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    with (
        patch("app.services.agent_stream.chat_completion", return_value=_answer_body("")),
        patch("app.services.agent_stream.chat_completion_stream", side_effect=_stream("")),
    ):
        events = list(stream_agent_answer(fake_db, ask))

    assert [e.type for e in events] == ["text", "done"]
    assert events[0].delta == "回答を生成できませんでした。もう一度お試しください。"


# --- llm_client（SSEの解釈） ---


def test_sseの行をチャンクに変換しusageを拾う() -> None:
    """`stream_options.include_usage` を付けないと usage は一切来ない。"""
    lines = [
        'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}',
        'data: {"choices":[{"delta":{"content":"在庫"},"finish_reason":null}]}',
        "data: 壊れた行",
        'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":7293,"completion_tokens":400}}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"来ないはず"}}]}',
    ]
    body = "".join(f"{line}\n\n" for line in lines).encode()

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        assert payload["max_tokens"] == 400
        return httpx.Response(200, content=body)

    def _fake_client(**kwargs: Any) -> httpx.Client:
        return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(_handler), **kwargs)

    with (
        patch(
            "app.services.llm_client.get_settings",
            return_value=Settings(base_url="http://llm.test/v1", model_name="test-model"),
        ),
        patch("app.services.llm_client.httpx.Client", _fake_client),
    ):
        chunks = list(chat_completion_stream([{"role": "user", "content": "在庫"}], max_tokens=400))

    assert [c.delta for c in chunks if c.delta] == ["在庫"]
    assert chunks[-1].prompt_tokens == 7293
    assert chunks[-1].completion_tokens == 400
    assert any(c.finish_reason == "length" for c in chunks)


def test_到達できない場合はLlmRequestErrorになる() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("接続できません")

    def _fake_client(**kwargs: Any) -> httpx.Client:
        return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(_handler), **kwargs)

    with (
        patch(
            "app.services.llm_client.get_settings",
            return_value=Settings(base_url="http://llm.test/v1", model_name="test-model"),
        ),
        patch("app.services.llm_client.httpx.Client", _fake_client),
        pytest.raises(LlmRequestError),
    ):
        list(chat_completion_stream([{"role": "user", "content": "在庫"}]))
