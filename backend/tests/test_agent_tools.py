"""Agent Toolの契約と、ID/FK取得時の安全条件。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.agent import (
    GetCallSummaryToolArgs,
    GetUtteranceContextToolArgs,
    KnowledgeSearchFilters,
    SearchKnowledgeToolArgs,
)
from app.models.tables import (
    CallSummaryTable,
    DataSourceTable,
    KnowledgeEvidenceTable,
    KnowledgeUnitTable,
    UtteranceSegmentTable,
)
from app.services.agent_tools import (
    AGENT_TOOL_DEFINITIONS,
    GET_CALL_SUMMARY,
    GET_KNOWLEDGE_EVIDENCE,
    GET_UTTERANCE_CONTEXT,
    SEARCH_KNOWLEDGE,
    _get_call_summary,
    _get_utterance_context,
    _search_knowledge,
    agent_tool_result_json,
    execute_agent_tool,
)


def test_agentに公開するtoolは4種類だけ() -> None:
    names = [item["function"]["name"] for item in AGENT_TOOL_DEFINITIONS]
    assert names == [
        SEARCH_KNOWLEDGE,
        GET_KNOWLEDGE_EVIDENCE,
        GET_CALL_SUMMARY,
        GET_UTTERANCE_CONTEXT,
    ]


def test_get系toolに検索queryやembedding引数がない() -> None:
    for item in AGENT_TOOL_DEFINITIONS[1:]:
        properties = item["function"]["parameters"]["properties"]
        assert "query" not in properties
        assert "embedding" not in properties


def test_search_knowledgeだけがqueryを要求する() -> None:
    definition = AGENT_TOOL_DEFINITIONS[0]["function"]
    assert "query" in definition["parameters"]["required"]
    assert definition["parameters"]["properties"]["top_k"]["maximum"] == 20


@patch("app.services.search.search_knowledge")
def test_search_toolはhybrid_searchサービスへ委譲する(search: MagicMock) -> None:
    item = MagicMock()
    item.model_dump.return_value = {"id": str(uuid4()), "title": "段階導入"}
    search.return_value = [item]
    db = MagicMock()

    result = _search_knowledge(
        db,
        SearchKnowledgeToolArgs(
            query="製造業で段階導入した事例",
            top_k=3,
            filters=KnowledgeSearchFilters(industry="製造業"),
        ),
    )

    assert result["count"] == 1
    search.assert_called_once()
    call = search.call_args
    assert call.args == (db, "製造業で段階導入した事例", 3)
    assert call.kwargs["filters"].industry == "製造業"


def test_不明なtoolは構造化エラーを返す() -> None:
    result = execute_agent_tool(MagicMock(), "search_everything", {})
    assert result["ok"] is False
    assert result["error"]["code"] == "unknown_tool"


def test_不正な引数はDBへ到達する前に拒否する() -> None:
    db = MagicMock()
    result = execute_agent_tool(db, GET_CALL_SUMMARY, {"data_source_id": "not-a-uuid"})
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    db.get.assert_not_called()


def test_要約未生成はエラーではなくfound_falseを返す() -> None:
    source_id = uuid4()
    db = MagicMock()
    db.get.return_value = SimpleNamespace(id=source_id)
    db.execute.return_value.scalar_one_or_none.return_value = None

    result = _get_call_summary(db, GetCallSummaryToolArgs(data_source_id=source_id))

    assert result == {
        "data_source_id": str(source_id),
        "found": False,
        "summary": None,
    }


def test_発言範囲の出典が異なる場合は拒否する() -> None:
    start_id, end_id = uuid4(), uuid4()
    start = SimpleNamespace(id=start_id, data_source_id=uuid4(), sequence_no=2)
    end = SimpleNamespace(id=end_id, data_source_id=uuid4(), sequence_no=3)
    db = MagicMock()
    db.get.side_effect = [start, end]

    args = GetUtteranceContextToolArgs(
        start_utterance_id=start_id,
        end_utterance_id=end_id,
    )
    result = execute_agent_tool(db, GET_UTTERANCE_CONTEXT, args.model_dump(mode="json"))

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_utterance_range"
    db.execute.assert_not_called()


def test_前後文脈を取得して根拠範囲を識別する() -> None:
    source_id = uuid4()
    start_id, end_id = uuid4(), uuid4()
    start = SimpleNamespace(id=start_id, data_source_id=source_id, sequence_no=3)
    end = SimpleNamespace(id=end_id, data_source_id=source_id, sequence_no=4)
    rows = [
        SimpleNamespace(
            id=uuid4(),
            data_source_id=source_id,
            sequence_no=sequence_no,
            speaker="speaker",
            start_sec=float(sequence_no),
            end_sec=float(sequence_no) + 0.5,
            content=f"発言{sequence_no}",
        )
        for sequence_no in range(2, 6)
    ]

    db = MagicMock()
    db.get.side_effect = [start, end]
    db.execute.return_value.scalars.return_value.all.return_value = rows
    result = _get_utterance_context(
        db,
        GetUtteranceContextToolArgs(
            start_utterance_id=start_id,
            end_utterance_id=end_id,
            before=1,
            after=1,
        ),
    )

    assert result["context_start_sequence_no"] == 2
    assert result["context_end_sequence_no"] == 5
    assert [row["is_evidence"] for row in result["utterances"]] == [False, True, True, False]


def test_tool結果は日本語をエスケープせずJSON化する() -> None:
    payload = agent_tool_result_json({"ok": True, "result": {"summary": "商談の要約"}})
    assert "商談の要約" in payload


def test_get系toolは5テーブルをIDとFKで辿る(db: Session) -> None:
    source = DataSourceTable(source_type="audio", file_name="agent-tool-test.wav")
    db.add(source)
    db.flush()

    segments = [
        UtteranceSegmentTable(
            data_source_id=source.id,
            sequence_no=sequence_no,
            speaker="customer" if sequence_no % 2 else "salesperson",
            start_sec=float(sequence_no - 1),
            end_sec=float(sequence_no),
            content=f"発言{sequence_no}",
        )
        for sequence_no in range(1, 6)
    ]
    db.add_all(segments)
    db.flush()

    knowledge = KnowledgeUnitTable(
        data_source_id=source.id,
        title="段階導入の提案",
        situation="一括導入に懸念がある",
        status="confirmed",
    )
    db.add(knowledge)
    db.flush()
    db.add(
        KnowledgeEvidenceTable(
            knowledge_id=knowledge.id,
            start_utterance_id=segments[1].id,
            end_utterance_id=segments[2].id,
        )
    )
    db.add(
        CallSummaryTable(
            data_source_id=source.id,
            summary="段階導入について合意した。",
            customer_needs=["現場負荷を抑えたい"],
            proposals=["段階導入"],
            decisions=["小規模から開始"],
            next_actions=["対象部署を決める"],
        )
    )
    db.flush()

    evidence = execute_agent_tool(
        db,
        GET_KNOWLEDGE_EVIDENCE,
        {"knowledge_id": str(knowledge.id)},
    )
    assert evidence["ok"] is True
    assert [u["sequence_no"] for u in evidence["result"]["spans"][0]["utterances"]] == [
        2,
        3,
    ]

    summary = execute_agent_tool(
        db,
        GET_CALL_SUMMARY,
        {"data_source_id": str(source.id)},
    )
    assert summary["ok"] is True
    assert summary["result"]["summary"]["proposals"] == ["段階導入"]

    context = execute_agent_tool(
        db,
        GET_UTTERANCE_CONTEXT,
        {
            "start_utterance_id": str(segments[1].id),
            "end_utterance_id": str(segments[2].id),
            "before": 1,
            "after": 1,
        },
    )
    assert context["ok"] is True
    assert [u["sequence_no"] for u in context["result"]["utterances"]] == [1, 2, 3, 4]
    assert [u["is_evidence"] for u in context["result"]["utterances"]] == [
        False,
        True,
        True,
        False,
    ]
