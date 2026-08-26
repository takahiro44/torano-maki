"""Qwen の Tool Calling を回して、出典つきの回答を作る。

**なぜループが要るか。**
Qwen は質問を受けても即答しない。まずナレッジを検索し、必要なら根拠の
発話や商談要約を追加で取りに行く。その1回ごとに vLLM とのやりとりが
発生するため、「応答を見る → Tool を実行する → 結果を返す」を
回答が出るまで繰り返す必要がある。

**出典は本文から拾わない。**
実際に実行した Tool の結果だけから組み立てる。LLM に出典を書かせると
存在しないIDやタイトルを作る。利用者が検証できない出典は無いのと同じ
（CLAUDE.md 6章）。

**止まらないことを前提に上限を置く。**
Tool を呼び続けて終わらない応答は実際に起きる。上限に達したら
Tool を外してもう一度だけ聞き、必ず文章で終わらせる。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.chat import (
    ChatMessage,
    ChatUsage,
    Citation,
    CitationUtterance,
    ToolTraceStep,
)
from app.models.tables import DataSourceTable
from app.services.agent_tools import (
    AGENT_TOOL_DEFINITIONS,
    GET_CALL_SUMMARY,
    GET_KNOWLEDGE_EVIDENCE,
    GET_UTTERANCE_CONTEXT,
    SEARCH_KNOWLEDGE,
    agent_tool_result_json,
    execute_agent_tool,
)
from app.services.llm_client import chat_completion

logger = logging.getLogger(__name__)

# Tool 呼び出しの往復上限。
# 4種類の Tool を順に使っても4回で足り、余裕を見て6回にしている。
# 上限を設けないと、同じ Tool を延々と呼び続ける応答で永久に返らなくなる。
MAX_ITERATIONS = 6

# 回答の再現性を上げるため低めにする。0にしないのは、
# 同じ質問に毎回まったく同じ言い回しを返すと会話として不自然なため。
TEMPERATURE = 0.3

SYSTEM_PROMPT = """あなたは営業ナレッジの検索アシスタントです。
社内に蓄積された商談ナレッジをもとに、営業担当者の質問に答えます。

## 手順

1. 質問に答えるには、まず search_knowledge でナレッジを検索してください。
2. 検索結果だけでは根拠が足りないと判断した場合にのみ、
   get_knowledge_evidence で元の発言を確認してください。
3. 商談全体の背景が必要な場合にのみ get_call_summary を使ってください。
4. 発言の前後の流れが必要な場合にのみ get_utterance_context を使ってください。

## ルール

- **Tool で取得した内容だけを根拠にしてください。** 一般論や推測で補わないこと。
- 検索しても関連するナレッジが無い場合は、
  「蓄積されたナレッジには該当する情報がありません」と正直に答えてください。
  それらしい答えを作らないこと。
- 回答は日本語で、営業担当者がそのまま使える具体性で書いてください。
- **出典（IDやファイル名）を本文に書かないでください。** 出典は別途システムが付けます。
- 挨拶や雑談には Tool を使わず、そのまま短く応じてください。

## 案件の分離（重要）

- 検索で複数のナレッジが返っても、**異なる顧客・商談・案件の事実を1つの話に混ぜない**こと。
- A社の状況・行動・結果を、B社や「田中製作所」など別案件の話に接続しないこと。
- 複数案件が参考になる場合は、見出しや番号で案件ごとに分けて書くこと。
  各案件の先頭で、ナレッジにある会社名・状況の手がかりを明示すること。
- 質問が特定の顧客（例: A社）に限定されている場合は、その顧客に関するナレッジだけを使い、
  他社の事例を本文に混ぜないこと。他社が参考になる場合は「別案件の参考」として明示的に分けること。
- 1つの段落の中で、複数の会社名・金額・結果をまたがないこと。
"""


@dataclass
class AgentLoopResult:
    answer: str
    citations: list[Citation]
    tool_trace: list[ToolTraceStep]
    usage: ChatUsage


@dataclass
class _CitationDraft:
    """Tool の実行結果を集めていく途中の出典。

    検索で見つかった時点では発話が無く、あとから
    get_knowledge_evidence が呼ばれたときに埋まる。
    そのため Pydantic ではなく、書き換え可能な入れ物にしている。
    """

    knowledge_id: UUID
    title: str
    data_source_id: UUID | None = None
    utterances: list[CitationUtterance] = field(default_factory=list)


def run_agent_loop(
    db: Session,
    messages: list[ChatMessage],
    *,
    top_k: int = 5,
    max_iterations: int = MAX_ITERATIONS,
) -> AgentLoopResult:
    """会話履歴を受け取り、Tool を使って回答を作る。"""
    conversation: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    conversation.extend({"role": m.role.value, "content": m.content} for m in messages)

    drafts: dict[UUID, _CitationDraft] = {}
    trace: list[ToolTraceStep] = []
    prompt_tokens = 0
    completion_tokens = 0
    iterations = 0
    answer: str | None = None
    hit_max = False

    while iterations < max_iterations:
        iterations += 1
        body = chat_completion(conversation, tools=AGENT_TOOL_DEFINITIONS, temperature=TEMPERATURE)
        prompt_tokens += _usage(body, "prompt_tokens")
        completion_tokens += _usage(body, "completion_tokens")

        message = _message_of(body)
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            answer = (message.get("content") or "").strip()
            break

        # Tool 結果を返すには、どの呼び出しへの応答かを示すため
        # assistant の tool_calls をそのまま履歴に残す必要がある
        conversation.append(
            {
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": tool_calls,
            }
        )

        for call in tool_calls:
            step = _execute_call(db, call, top_k=top_k, step_no=len(trace) + 1)
            trace.append(step.trace)
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": agent_tool_result_json(step.raw_result),
                }
            )
            _collect_citations(step.raw_result, drafts)
    else:
        # while が break せずに上限へ達した場合。
        # Tool を外して聞き直し、必ず文章で終わらせる
        hit_max = True
        logger.warning("Agent Loop が上限 %d 回に達しました", max_iterations)
        conversation.append(
            {
                "role": "user",
                "content": "これ以上は調べずに、ここまでで分かったことだけで回答してください。",
            }
        )
        body = chat_completion(conversation, temperature=TEMPERATURE)
        prompt_tokens += _usage(body, "prompt_tokens")
        completion_tokens += _usage(body, "completion_tokens")
        answer = (_message_of(body).get("content") or "").strip()

    if not answer:
        answer = "回答を生成できませんでした。もう一度お試しください。"

    return AgentLoopResult(
        answer=answer,
        citations=_finalize_citations(db, drafts),
        tool_trace=trace,
        usage=ChatUsage(
            iterations=iterations,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            hit_max_iterations=hit_max,
        ),
    )


@dataclass
class _ExecutedCall:
    trace: ToolTraceStep
    raw_result: dict[str, Any]


def _execute_call(db: Session, call: dict[str, Any], *, top_k: int, step_no: int) -> _ExecutedCall:
    function = call.get("function") or {}
    name = str(function.get("name") or "")
    arguments = function.get("arguments") or {}

    if name == SEARCH_KNOWLEDGE:
        arguments = _force_top_k(arguments, top_k)

    result = execute_agent_tool(db, name, arguments)
    ok = bool(result.get("ok"))
    return _ExecutedCall(
        trace=ToolTraceStep(
            step=step_no,
            tool=name or "(名前なし)",
            ok=ok,
            summary=_summarize(name, result),
            error_code=None if ok else str((result.get("error") or {}).get("code")),
        ),
        raw_result=result,
    )


def _force_top_k(arguments: str | dict[str, Any], top_k: int) -> dict[str, Any]:
    """検索件数はリクエストの値で固定する。

    件数はクライアントが決めるべき表示上の都合であり、モデルの判断で
    毎回変わると同じ質問でも結果の量がぶれる。
    引数がJSONとして壊れている場合は触らず、そのまま検証側へ渡して
    `invalid_arguments` として扱わせる。
    """
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"__invalid__": arguments}
        arguments = parsed
    if not isinstance(arguments, dict):
        return {"__invalid__": arguments}
    return {**arguments, "top_k": top_k}


def _message_of(body: dict[str, Any]) -> dict[str, Any]:
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        logger.error("vLLM の応答形式が想定外です: %r", body)
        return {}
    return message if isinstance(message, dict) else {}


def _usage(body: dict[str, Any], key: str) -> int:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get(key)
    return value if isinstance(value, int) else 0


def _summarize(name: str, result: dict[str, Any]) -> str:
    """画面にそのまま出せる1行を作る。"""
    if not result.get("ok"):
        error = result.get("error") or {}
        return f"失敗: {error.get('message') or '不明なエラー'}"

    payload = result.get("result") or {}
    if name == SEARCH_KNOWLEDGE:
        return f"ナレッジを検索しました（{payload.get('count', 0)}件）"
    if name == GET_KNOWLEDGE_EVIDENCE:
        return f"根拠の発言を取得しました（{payload.get('count', 0)}区間）"
    if name == GET_CALL_SUMMARY:
        return "商談要約を取得しました" if payload.get("found") else "商談要約はありませんでした"
    if name == GET_UTTERANCE_CONTEXT:
        return f"前後の発言を取得しました（{len(payload.get('utterances') or [])}件）"
    return "実行しました"


def _collect_citations(result: dict[str, Any], drafts: dict[UUID, _CitationDraft]) -> None:
    """Tool の実行結果から出典を積み上げる。

    検索で見つかったものだけを候補にし、根拠の発話は
    get_knowledge_evidence が呼ばれた場合にだけ足す。
    Agent が触れていないナレッジを出典に混ぜない。
    """
    if not result.get("ok"):
        return
    payload = result.get("result") or {}
    tool = result.get("tool")

    if tool == SEARCH_KNOWLEDGE:
        for item in payload.get("items") or []:
            knowledge_id = _as_uuid(item.get("id"))
            if knowledge_id is None or knowledge_id in drafts:
                continue
            drafts[knowledge_id] = _CitationDraft(
                knowledge_id=knowledge_id,
                title=str(item.get("title") or ""),
                data_source_id=_as_uuid(item.get("data_source_id")),
            )
        return

    if tool == GET_KNOWLEDGE_EVIDENCE:
        knowledge_id = _as_uuid(payload.get("knowledge_id"))
        draft = drafts.get(knowledge_id) if knowledge_id is not None else None
        if draft is None:
            return
        for span in payload.get("spans") or []:
            for utterance in span.get("utterances") or []:
                draft.utterances.append(
                    CitationUtterance(
                        sequence_no=int(utterance["sequence_no"]),
                        speaker=str(utterance["speaker"]),
                        start_sec=float(utterance["start_sec"]),
                        end_sec=float(utterance["end_sec"]),
                        content=str(utterance["content"]),
                    )
                )


def _finalize_citations(db: Session, drafts: dict[UUID, _CitationDraft]) -> list[Citation]:
    """出典に入力元の情報を付ける。

    `Knowledge.source_type` は現在 "manual" 固定で返るため使えない。
    実際の種別（audio など）は data_sources を引かないと分からない。
    """
    source_ids = {d.data_source_id for d in drafts.values() if d.data_source_id is not None}
    sources: dict[UUID, DataSourceTable] = {}
    for source_id in source_ids:
        row = db.get(DataSourceTable, source_id)
        if row is not None:
            sources[source_id] = row

    citations: list[Citation] = []
    for draft in drafts.values():
        source = sources.get(draft.data_source_id) if draft.data_source_id else None
        citations.append(
            Citation(
                knowledge_id=draft.knowledge_id,
                title=draft.title,
                data_source_id=draft.data_source_id,
                source_type=source.source_type if source else None,
                file_name=source.file_name if source else None,
                utterances=draft.utterances,
            )
        )
    return citations


def _as_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None
