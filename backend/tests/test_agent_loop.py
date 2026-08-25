"""Agent Loop の制御と、出典の組み立て。

**vLLM はモックする。** DGXが落ちていてもCIを回せるようにするためと、
生成が非決定的で「Toolを2回呼ぶ」といった経路を狙って再現できないため。
ここで確かめたいのはモデルの賢さではなく、
**返ってきた tool_calls をこちらが正しく捌けているか**。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatRole
from app.models.tables import DataSourceTable
from app.services.agent_loop import (
    GET_KNOWLEDGE_EVIDENCE,
    SEARCH_KNOWLEDGE,
    run_agent_loop,
)


def _answer(content: str, *, prompt: int = 100, completion: int = 20) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


def _tool_call(name: str, arguments: dict[str, Any], *, call_id: str = "call-1") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def _ok(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "tool": tool, "result": result}


def _search_result(knowledge_id: str, title: str, data_source_id: str) -> dict[str, Any]:
    return {
        "query": "在庫",
        "count": 1,
        "items": [{"id": knowledge_id, "title": title, "data_source_id": data_source_id}],
    }


@pytest.fixture
def ask() -> list[ChatMessage]:
    return [ChatMessage(role=ChatRole.USER, content="在庫の食い違いにどう対応した？")]


@pytest.fixture
def fake_db() -> MagicMock:
    """入力元を引けない Session の代わり。

    素の MagicMock は `db.get()` にも MagicMock を返すため、出典の組み立てが
    「DataSourceが見つかった」経路へ入り、実DBと違う挙動になる。
    見つからない場合は None が返るのが本来の姿。
    """
    db = MagicMock()
    db.get.return_value = None
    return db


def test_toolを呼ばない質問はそのまま回答する(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    """挨拶などで毎回検索が走ると、無駄に10秒待たされる。"""
    with patch("app.services.agent_loop.chat_completion", return_value=_answer("こんにちは")):
        result = run_agent_loop(fake_db, ask)

    assert result.answer == "こんにちは"
    assert result.tool_trace == []
    assert result.citations == []
    assert result.usage.iterations == 1
    assert result.usage.hit_max_iterations is False


def test_tool呼び出し1回で回答する(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    responses = [
        _tool_call(SEARCH_KNOWLEDGE, {"query": "在庫"}),
        _answer("在庫の非同期については…"),
    ]
    tool_result = _ok(SEARCH_KNOWLEDGE, _search_result(str(uuid4()), "在庫リスク", str(uuid4())))

    with (
        patch("app.services.agent_loop.chat_completion", side_effect=responses),
        patch("app.services.agent_loop.execute_agent_tool", return_value=tool_result),
    ):
        result = run_agent_loop(fake_db, ask)

    assert result.answer == "在庫の非同期については…"
    assert len(result.tool_trace) == 1
    assert result.tool_trace[0].tool == SEARCH_KNOWLEDGE
    assert result.tool_trace[0].ok is True
    assert "1件" in result.tool_trace[0].summary
    assert result.usage.iterations == 2
    assert result.usage.prompt_tokens == 200


def test_tool呼び出しが連鎖する(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    """検索 → 根拠取得 → 回答、と複数往復できること。"""
    knowledge_id = str(uuid4())
    source_id = str(uuid4())
    responses = [
        _tool_call(SEARCH_KNOWLEDGE, {"query": "在庫"}, call_id="c1"),
        _tool_call(GET_KNOWLEDGE_EVIDENCE, {"knowledge_id": knowledge_id}, call_id="c2"),
        _answer("元の発言ではこう言っています…"),
    ]
    tool_results = [
        _ok(SEARCH_KNOWLEDGE, _search_result(knowledge_id, "在庫リスク", source_id)),
        _ok(GET_KNOWLEDGE_EVIDENCE, {"knowledge_id": knowledge_id, "count": 2, "spans": []}),
    ]

    with (
        patch("app.services.agent_loop.chat_completion", side_effect=responses),
        patch("app.services.agent_loop.execute_agent_tool", side_effect=tool_results),
    ):
        result = run_agent_loop(fake_db, ask)

    assert [t.tool for t in result.tool_trace] == [SEARCH_KNOWLEDGE, GET_KNOWLEDGE_EVIDENCE]
    assert [t.step for t in result.tool_trace] == [1, 2]
    assert "2区間" in result.tool_trace[1].summary
    assert result.usage.iterations == 3


def test_上限に達したら打ち切って必ず文章で返す(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    """Toolを呼び続ける応答で永久に返らなくなるのを防ぐ。"""
    tool_result = _ok(SEARCH_KNOWLEDGE, _search_result(str(uuid4()), "在庫", str(uuid4())))

    def _always_tool(messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        # Tool を外して呼ばれた最後の1回だけ文章を返す
        if not kwargs.get("tools"):
            return _answer("ここまでで分かったことは…")
        return _tool_call(SEARCH_KNOWLEDGE, {"query": "在庫"})

    with (
        patch("app.services.agent_loop.chat_completion", side_effect=_always_tool),
        patch("app.services.agent_loop.execute_agent_tool", return_value=tool_result),
    ):
        result = run_agent_loop(fake_db, ask, max_iterations=2)

    assert result.usage.hit_max_iterations is True
    assert result.usage.iterations == 2
    assert result.answer == "ここまでで分かったことは…"
    assert len(result.tool_trace) == 2


def test_検索件数はリクエストのtop_kで固定する(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    """モデルが毎回違う件数を要求すると、同じ質問でも結果の量がぶれる。"""
    responses = [
        _tool_call(SEARCH_KNOWLEDGE, {"query": "在庫", "top_k": 20}),
        _answer("回答"),
    ]
    tool_result = _ok(SEARCH_KNOWLEDGE, {"query": "在庫", "count": 0, "items": []})

    with (
        patch("app.services.agent_loop.chat_completion", side_effect=responses),
        patch("app.services.agent_loop.execute_agent_tool", return_value=tool_result) as execute,
    ):
        run_agent_loop(fake_db, ask, top_k=3)

    passed_arguments = execute.call_args.args[2]
    assert passed_arguments["top_k"] == 3


def test_tool失敗はtraceに残り回答は続行する(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    """1つのToolが失敗しても、Agentは残りの材料で答えられる。"""
    responses = [
        _tool_call(GET_KNOWLEDGE_EVIDENCE, {"knowledge_id": str(uuid4())}),
        _answer("根拠は取得できませんでしたが…"),
    ]
    failure = {
        "ok": False,
        "tool": GET_KNOWLEDGE_EVIDENCE,
        "error": {"code": "knowledge_not_found", "message": "確認済みKnowledgeが見つかりません"},
    }

    with (
        patch("app.services.agent_loop.chat_completion", side_effect=responses),
        patch("app.services.agent_loop.execute_agent_tool", return_value=failure),
    ):
        result = run_agent_loop(fake_db, ask)

    assert result.tool_trace[0].ok is False
    assert result.tool_trace[0].error_code == "knowledge_not_found"
    assert result.tool_trace[0].summary.startswith("失敗:")
    assert result.citations == []
    assert result.answer


def test_出典は実行したtoolの結果からのみ作る(db: Session, ask: list[ChatMessage]) -> None:
    """LLMの本文からは出典を拾わない。捏造されたIDを通さないため。"""
    source = DataSourceTable(source_type="audio", file_name="sales_demo.wav")
    db.add(source)
    db.flush()

    knowledge_id = str(uuid4())
    responses = [
        _tool_call(SEARCH_KNOWLEDGE, {"query": "在庫"}, call_id="c1"),
        _tool_call(GET_KNOWLEDGE_EVIDENCE, {"knowledge_id": knowledge_id}, call_id="c2"),
        # 本文に別のIDを書かせても出典には出ない
        _answer(f"出典は {uuid4()} です"),
    ]
    tool_results = [
        _ok(SEARCH_KNOWLEDGE, _search_result(knowledge_id, "在庫リスク", str(source.id))),
        _ok(
            GET_KNOWLEDGE_EVIDENCE,
            {
                "knowledge_id": knowledge_id,
                "count": 1,
                "spans": [
                    {
                        "utterances": [
                            {
                                "sequence_no": 25,
                                "speaker": "salesperson",
                                "start_sec": 188.46,
                                "end_sec": 198.46,
                                "content": "システム上では在庫があるので…",
                            }
                        ]
                    }
                ],
            },
        ),
    ]

    with (
        patch("app.services.agent_loop.chat_completion", side_effect=responses),
        patch("app.services.agent_loop.execute_agent_tool", side_effect=tool_results),
    ):
        result = run_agent_loop(db, ask)

    assert len(result.citations) == 1
    citation = result.citations[0]
    assert str(citation.knowledge_id) == knowledge_id
    assert citation.title == "在庫リスク"
    # Knowledge.source_type は "manual" 固定で返るため、data_sources を引いている
    assert citation.source_type == "audio"
    assert citation.file_name == "sales_demo.wav"
    assert len(citation.utterances) == 1
    assert citation.utterances[0].speaker == "salesperson"
    assert citation.utterances[0].start_sec == 188.46


def test_検索していないナレッジの根拠は出典にしない(
    fake_db: MagicMock, ask: list[ChatMessage]
) -> None:
    """Agentが触れていないものを出典に混ぜない。"""
    responses = [
        _tool_call(GET_KNOWLEDGE_EVIDENCE, {"knowledge_id": str(uuid4())}),
        _answer("回答"),
    ]
    evidence = _ok(
        GET_KNOWLEDGE_EVIDENCE,
        {"knowledge_id": str(uuid4()), "count": 1, "spans": []},
    )

    with (
        patch("app.services.agent_loop.chat_completion", side_effect=responses),
        patch("app.services.agent_loop.execute_agent_tool", return_value=evidence),
    ):
        result = run_agent_loop(fake_db, ask)

    assert result.citations == []


def test_空の応答でも文言を返す(fake_db: MagicMock, ask: list[ChatMessage]) -> None:
    """content が null のまま終わる応答が実際にありうる。"""
    with patch("app.services.agent_loop.chat_completion", return_value=_answer("")):
        result = run_agent_loop(fake_db, ask)

    assert result.answer
