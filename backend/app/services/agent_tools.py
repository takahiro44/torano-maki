"""Qwen Agent Loop に公開する Tool 定義と実行処理。

一次検索は ``search_knowledge`` だけが行う。残りの Tool は、検索済み
Knowledge の ``knowledge_id`` / ``data_source_id`` と外部キーを使って
追加文脈を取得し、Semantic Search は行わない。

**根拠の取得と整合性チェックは ``services/knowledge_context.py`` にある。**
同じ処理をロープレでも使うため、ここには「Tool の引数を検証して
結果をJSONにする」ことだけを残している（計画書5章）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models.agent import (
    GetCallSummaryToolArgs,
    GetKnowledgeEvidenceToolArgs,
    GetUtteranceContextToolArgs,
    SearchKnowledgeToolArgs,
)
from app.models.tables import DataSourceTable
from app.services.knowledge_context import (
    ContextUtterance,
    KnowledgeContextError,
    get_call_summary,
    get_confirmed_knowledge,
    get_evidence_spans,
    get_utterance_window,
)

SEARCH_KNOWLEDGE = "search_knowledge"
GET_KNOWLEDGE_EVIDENCE = "get_knowledge_evidence"
GET_CALL_SUMMARY = "get_call_summary"
GET_UTTERANCE_CONTEXT = "get_utterance_context"


class AgentToolError(RuntimeError):
    """Tool がAgentへ返せる、想定内のエラー。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _openai_tool(name: str, description: str, args_model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": args_model.model_json_schema(),
        },
    }


AGENT_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _openai_tool(
        SEARCH_KNOWLEDGE,
        "ユーザー質問に関連する再利用可能な営業Knowledgeを検索する。"
        "会話の最初に使う一次検索Tool。Semantic SearchとLexical SearchをRRFで統合する。",
        SearchKnowledgeToolArgs,
    ),
    _openai_tool(
        GET_KNOWLEDGE_EVIDENCE,
        "取得済みKnowledgeの根拠となった発言範囲をIDで取得する。"
        "Knowledgeだけでは根拠が不足すると判断した場合にのみ使う。",
        GetKnowledgeEvidenceToolArgs,
    ),
    _openai_tool(
        GET_CALL_SUMMARY,
        "取得済みKnowledgeと同じ入力元の商談要約をdata_source_idで取得する。"
        "商談全体の背景が必要な場合にのみ使う。Semantic Searchは行わない。",
        GetCallSummaryToolArgs,
    ),
    _openai_tool(
        GET_UTTERANCE_CONTEXT,
        "発言IDまたはEvidence範囲の前後にある発言を時系列で取得する。"
        "元発言の会話文脈が必要な場合にのみ使う。Semantic Searchは行わない。",
        GetUtteranceContextToolArgs,
    ),
]


def _utterance_json(row: ContextUtterance) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "data_source_id": str(row.data_source_id),
        "sequence_no": row.sequence_no,
        "speaker": row.speaker,
        "start_sec": row.start_sec,
        "end_sec": row.end_sec,
        "content": row.content,
        "is_evidence": row.is_evidence,
    }


def _search_knowledge(db: Session, args: SearchKnowledgeToolArgs) -> dict[str, Any]:
    # Hybrid Search はClaude Code側の担当なので、公開済みのサービス境界だけを呼ぶ。
    from app.services.search import KnowledgeFilter, search_knowledge

    filters = None
    if args.filters is not None:
        values = args.filters.model_dump(exclude_none=True)
        if values:
            filters = KnowledgeFilter(**values)

    rows = search_knowledge(db, args.query, args.top_k, filters=filters)
    return {
        "query": args.query,
        "count": len(rows),
        "items": [row.model_dump(mode="json") for row in rows],
    }


def _get_knowledge_evidence(db: Session, args: GetKnowledgeEvidenceToolArgs) -> dict[str, Any]:
    knowledge = get_confirmed_knowledge(db, args.knowledge_id)
    # 前後文脈を足さないのは、Agent が根拠の境界を見誤らないようにするため。
    # 周辺が要る場合は Agent が get_utterance_context を選ぶ。
    spans = get_evidence_spans(db, knowledge)

    return {
        "knowledge_id": str(knowledge.id),
        "data_source_id": (
            str(knowledge.data_source_id) if knowledge.data_source_id is not None else None
        ),
        "count": len(spans),
        "spans": [
            {
                "evidence_id": str(span.evidence_id),
                "start_utterance_id": str(span.start_utterance_id),
                "end_utterance_id": str(span.end_utterance_id),
                "start_sequence_no": span.start_sequence_no,
                "end_sequence_no": span.end_sequence_no,
                "utterances": [_utterance_json(row) for row in span.utterances],
            }
            for span in spans
        ],
    }


def _get_call_summary(db: Session, args: GetCallSummaryToolArgs) -> dict[str, Any]:
    source = db.get(DataSourceTable, args.data_source_id)
    if source is None:
        raise AgentToolError("data_source_not_found", "DataSourceが見つかりません")

    row = get_call_summary(db, args.data_source_id)
    if row is None:
        return {
            "data_source_id": str(args.data_source_id),
            "found": False,
            "summary": None,
        }
    return {
        "data_source_id": str(row.data_source_id),
        "found": True,
        "summary": {
            "id": str(row.id),
            "summary": row.summary,
            "customer_needs": row.customer_needs,
            "proposals": row.proposals,
            "decisions": row.decisions,
            "next_actions": row.next_actions,
            "created_at": row.created_at.isoformat(),
        },
    }


def _get_utterance_context(db: Session, args: GetUtteranceContextToolArgs) -> dict[str, Any]:
    window = get_utterance_window(
        db,
        args.start_utterance_id,
        args.end_utterance_id,
        before=args.before,
        after=args.after,
    )
    return {
        "data_source_id": str(window.data_source_id),
        "evidence_start_sequence_no": window.evidence_start_sequence_no,
        "evidence_end_sequence_no": window.evidence_end_sequence_no,
        "context_start_sequence_no": window.context_start_sequence_no,
        "context_end_sequence_no": window.context_end_sequence_no,
        "utterances": [_utterance_json(row) for row in window.utterances],
    }


ToolArgs = (
    SearchKnowledgeToolArgs
    | GetKnowledgeEvidenceToolArgs
    | GetCallSummaryToolArgs
    | GetUtteranceContextToolArgs
)
ToolExecutor = Callable[[Session, Any], dict[str, Any]]

_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    SEARCH_KNOWLEDGE: SearchKnowledgeToolArgs,
    GET_KNOWLEDGE_EVIDENCE: GetKnowledgeEvidenceToolArgs,
    GET_CALL_SUMMARY: GetCallSummaryToolArgs,
    GET_UTTERANCE_CONTEXT: GetUtteranceContextToolArgs,
}

_EXECUTORS: dict[str, ToolExecutor] = {
    SEARCH_KNOWLEDGE: _search_knowledge,
    GET_KNOWLEDGE_EVIDENCE: _get_knowledge_evidence,
    GET_CALL_SUMMARY: _get_call_summary,
    GET_UTTERANCE_CONTEXT: _get_utterance_context,
}


def execute_agent_tool(
    db: Session,
    tool_name: str,
    arguments: str | dict[str, Any],
) -> dict[str, Any]:
    """Qwenが返したTool名と引数を検証して実行する。

    想定内の入力・参照エラーは、Agentが次の判断に使える構造化結果として返す。
    DB接続障害など想定外の例外は隠さず呼び出し側へ送出する。
    """
    args_model = _ARGUMENT_MODELS.get(tool_name)
    executor = _EXECUTORS.get(tool_name)
    if args_model is None or executor is None:
        return {
            "ok": False,
            "tool": tool_name,
            "error": {"code": "unknown_tool", "message": "未定義のToolです"},
        }

    try:
        raw_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        parsed: ToolArgs = args_model.model_validate(raw_arguments)  # type: ignore[assignment]
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return {
            "ok": False,
            "tool": tool_name,
            "error": {"code": "invalid_arguments", "message": str(exc)},
        }

    try:
        result = executor(db, parsed)
    except (AgentToolError, KnowledgeContextError) as exc:
        return {
            "ok": False,
            "tool": tool_name,
            "error": {"code": exc.code, "message": exc.message},
        }
    return {"ok": True, "tool": tool_name, "result": result}


def agent_tool_result_json(result: dict[str, Any]) -> str:
    """Tool実行結果をQwenへ返す ``role=tool`` の文字列にする。"""
    return json.dumps(result, ensure_ascii=False)
