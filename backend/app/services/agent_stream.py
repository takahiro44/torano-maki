"""Agent Loop のストリーミング版。SSE で流すイベント列を作る。

**なぜ別ファイルか。** 既存 `agent_loop.py` は `POST /chat` が使っており、
壊すと非ストリーミングの利用者ごと落ちる。制御の流れが
「結果を返す」から「イベントを産む」に変わるため差分も読みにくい。
判断（Tool の選択・出典の組み立て）は agent_loop から**インポートして共有**し、
ここには「いつ何を流すか」だけを置く。

**なぜストリーミングが要るか。** 実測で総時間32.8秒のうち大半は
回答を書いている時間だった（decode 約20 tok/s、最初のトークンまで1.2秒）。
プロンプトを半分に削っても総時間は変わらなかったため、
効くのは (1) 届いた分から流す (2) 出力トークンを減らす の2つだけ。

**Tool 呼び出しのラウンドはストリーミングしない。** `tool_calls` は
チャンクを跨いだ断片で届き、組み立てても得るものが無い。
最終回答のラウンドだけを `stream=True` で流す。
ただし「そのラウンドが最終回答か」は投げてみるまで分からないため、
2往復目以降はまずストリームで投げ、`tool_calls` が出たら
そのストリームを捨てて非ストリーミングで取り直す（下の _stream_answer）。

**捨てるときは `answer_reset` を流す。** モデルは「根拠を確認します」と
前置きしてから Tool を呼ぶことがあり、その前置きは既に画面へ流れている。
取り消さないと前置きが回答として確定してしまう。
システムプロンプト側でも前置きを書かないよう指示しているが、
守られない前提で取り消せるようにしておく。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator, Iterator
from contextlib import closing
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.chat import (
    ChatMessage,
    ChatStreamAnswerResetEvent,
    ChatStreamCitationsEvent,
    ChatStreamDoneEvent,
    ChatStreamEvent,
    ChatStreamTextEvent,
    ChatStreamToolCallEvent,
    ChatStreamToolResultEvent,
    ChatUsage,
)

# 判断のロジックは agent_loop が正。ここでコピーすると、
# 出典の作り方が2箇所に分かれて必ず食い違う（CLAUDE.md 6章）
from app.services.agent_loop import (
    MAX_ITERATIONS,
    TEMPERATURE,
    _CitationDraft,
    _collect_citations,
    _execute_call,
    _finalize_citations,
    _message_of,
    _usage,
)
from app.services.agent_loop import (
    SYSTEM_PROMPT as AGENT_SYSTEM_PROMPT,
)
from app.services.agent_tools import (
    AGENT_TOOL_DEFINITIONS,
    GET_CALL_SUMMARY,
    GET_KNOWLEDGE_EVIDENCE,
    GET_UTTERANCE_CONTEXT,
    SEARCH_KNOWLEDGE,
    agent_tool_result_json,
)
from app.services.llm_client import chat_completion, chat_completion_stream

logger = logging.getLogger(__name__)

# 回答の上限トークン。**これは安全網であって、長さの制御ではない。**
# 長さは下の長さ指示（400文字程度）が担当する。上限だけ設けると文の途中で切れる。
#
# 400 にしていたときは普通の回答が頻繁に打ち切られていた。実測の 919文字＝約560トークン
# （1トークン≒1.64文字）から、指示どおりの400文字でも約244トークンを使う。
# 見出しや箇条書きの記号、指示より少し長く書いた分で 400 は簡単に超える。
# 700 なら指示どおりの回答の約3倍まで許容でき、指示を無視した場合にだけ効く。
# decode が約20 tok/s なので上限まで書き切った最悪ケースで35秒だが、
# それは長さ指示が効かなかったときだけで、通常は250トークン前後で止まる。
MAX_ANSWER_TOKENS = 700

# 上限に当たって打ち切られた場合に足す一言。
# 何も足さないと、利用者は尻切れを「AIが言い淀んだ」と読んでしまう
TRUNCATED_SUFFIX = "\n\n（回答が長くなったため、ここで区切りました）"

# 既存の SYSTEM_PROMPT は `POST /chat` が使っているため変更しない。
# 長さの指示だけをこちら側で足す
_LENGTH_INSTRUCTION = """
## 回答の長さ

- **結論を最初の1〜2文で書いてください。** 前置きや質問の言い換えは不要です。
- 全体で400文字程度に収めてください。長くなる場合は要点だけを残すこと。
- 箇条書きは3〜5項目までにしてください。

## Tool を呼ぶとき

- **Tool を呼ぶ前に何も書かないでください。** 「確認します」「調べます」といった
  前置きは不要です。必要な Tool をそのまま呼んでください。
  回答は、調べ終わってから書き始めてください。
"""

SYSTEM_PROMPT = AGENT_SYSTEM_PROMPT + _LENGTH_INSTRUCTION

# Tool 名から画面に出す日本語へ。**対応表をサーバに置く**のは、
# Tool を増やしたときにフロントを直さずに済ませるため
_TOOL_LABELS: dict[str, str] = {
    SEARCH_KNOWLEDGE: "ナレッジを検索しています",
    GET_KNOWLEDGE_EVIDENCE: "根拠の発言を確認しています",
    GET_CALL_SUMMARY: "商談の要約を確認しています",
    GET_UTTERANCE_CONTEXT: "前後の発言を確認しています",
}
_UNKNOWN_TOOL_LABEL = "情報を取得しています"

_EMPTY_ANSWER = "回答を生成できませんでした。もう一度お試しください。"
_STOP_INVESTIGATING = "これ以上は調べずに、ここまでで分かったことだけで回答してください。"


@dataclass
class _Totals:
    """ラウンドを跨いでトークン数を足していく入れ物。

    `done` の usage は既存 ChatUsage と同じ値でなければならないが、
    ストリーミングでは usage が最終チャンクにしか来ない。
    数え漏らしを1箇所に閉じ込めるために持つ。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion


@dataclass
class _StreamedRound:
    """ストリームで回した1ラウンドの結果。"""

    saw_tool_calls: bool = False
    text: str = ""


def stream_agent_answer(
    db: Session,
    messages: list[ChatMessage],
    *,
    top_k: int = 5,
    max_iterations: int = MAX_ITERATIONS,
) -> Iterator[ChatStreamEvent]:
    """会話履歴を受け取り、進捗と回答をイベントとして順に返す。

    例外（`LlmNotConfiguredError` / `LlmRequestError`）はここでは握らない。
    ストリームは接続確立時点で 200 が確定しており、ステータスコードで
    失敗を表現できないため、`error` イベントへの変換は HTTP 層で行う。
    """
    conversation: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    conversation.extend({"role": m.role.value, "content": m.content} for m in messages)

    drafts: dict[UUID, _CitationDraft] = {}
    totals = _Totals()
    sent_citations: tuple[tuple[UUID, int], ...] = ()
    steps = 0
    iterations = 0
    hit_max = False
    answered_text = ""

    while iterations < max_iterations:
        iterations += 1

        if iterations == 1:
            # 1往復目はほぼ必ず Tool 呼び出しになる。ここをストリームで試すと
            # prefill を1回丸ごと捨てることになるので、最初から非ストリーミング
            message = _ask_for_tool_calls(conversation, totals)
        else:
            round_ = yield from _stream_answer(conversation, totals, tools=AGENT_TOOL_DEFINITIONS)
            if not round_.saw_tool_calls:
                answered_text = round_.text
                break
            # 前置きを流したあとに Tool 呼び出しが判明した場合。
            # 取り消さないと、前置きが回答の先頭に残ったまま本文が続く
            if round_.text.strip():
                yield ChatStreamAnswerResetEvent()
            message = _ask_for_tool_calls(conversation, totals)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            # 挨拶などで Tool を使わずに答えた場合。すでに全文が手元にあるので
            # 1イベントで流す（形はストリーミングと同じで、フロントの分岐は不要）
            answered_text = (message.get("content") or "").strip()
            if answered_text:
                yield ChatStreamTextEvent(delta=answered_text)
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
            steps += 1
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            # 実行**前**に流す。検索の数秒を無言にしないためのイベント
            yield ChatStreamToolCallEvent(
                step=steps,
                tool=name or "(名前なし)",
                label=_TOOL_LABELS.get(name, _UNKNOWN_TOOL_LABEL),
                arguments=_event_arguments(name, function.get("arguments"), top_k),
            )

            executed = _execute_call(db, call, top_k=top_k, step_no=steps)
            trace = executed.trace
            yield ChatStreamToolResultEvent(
                step=trace.step,
                tool=trace.tool,
                ok=trace.ok,
                summary=trace.summary,
                error_code=trace.error_code,
            )
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": agent_tool_result_json(executed.raw_result),
                }
            )

            _collect_citations(executed.raw_result, drafts)
            fingerprint = _citation_fingerprint(drafts)
            if fingerprint != sent_citations:
                sent_citations = fingerprint
                yield ChatStreamCitationsEvent(citations=_finalize_citations(db, drafts))
    else:
        # 上限に達した場合。Tool を外して聞き直し、必ず文章で終わらせる。
        # この最後の1回もストリームでよい（もう Tool は来ないため）
        hit_max = True
        logger.warning("Agent Loop が上限 %d 回に達しました", max_iterations)
        conversation.append({"role": "user", "content": _STOP_INVESTIGATING})
        round_ = yield from _stream_answer(conversation, totals, tools=None)
        answered_text = round_.text

    if not answered_text.strip():
        # **Tool も本文も返さないラウンドが実際に起きる。** モデルの揺れで、
        # 同じ質問でも数回に1回は空の応答が返る。ここで諦めると利用者には
        # 「回答を生成できませんでした」しか残らず、調べ終えた Tool の結果が
        # まるごと無駄になる。Tool を外して、必ず文章で答えさせる1回を挟む。
        logger.warning("回答が空だったため、Tool を外して聞き直します")
        conversation.append({"role": "user", "content": _STOP_INVESTIGATING})
        round_ = yield from _stream_answer(conversation, totals, tools=None)
        answered_text = round_.text

    if not answered_text.strip():
        yield ChatStreamTextEvent(delta=_EMPTY_ANSWER)

    yield ChatStreamDoneEvent(
        usage=ChatUsage(
            iterations=iterations,
            prompt_tokens=totals.prompt_tokens,
            completion_tokens=totals.completion_tokens,
            hit_max_iterations=hit_max,
        )
    )


def _ask_for_tool_calls(conversation: list[dict[str, Any]], totals: _Totals) -> dict[str, Any]:
    """Tool を選ばせるための1往復。**ここはストリーミングしない。**

    出力が短い（Tool 名と引数だけ）ため流しても体感は変わらず、
    `tool_calls` を断片から組み立てる危うさだけが残るため。
    """
    body = chat_completion(conversation, tools=AGENT_TOOL_DEFINITIONS, temperature=TEMPERATURE)
    totals.add(_usage(body, "prompt_tokens"), _usage(body, "completion_tokens"))
    return _message_of(body)


def _stream_answer(
    conversation: list[dict[str, Any]],
    totals: _Totals,
    *,
    tools: list[dict[str, Any]] | None,
) -> Generator[ChatStreamEvent, None, _StreamedRound]:
    """最終回答のつもりでストリームを開き、届いた分から `text` で流す。

    **Tool を呼ばれたら捨てる。** まだ調べる必要があるラウンドだったので、
    ここまでのチャンクは無視して呼び出し側に非ストリーミングで
    取り直してもらう。捨てるのは prefill 1回分（実測1.2秒）で、
    最終回答の20秒を流せる価値の方が大きい。

    **本文が流れ始めたあとの `tool_calls` も捨てない。** モデルは
    「根拠の発言を確認します」と前置きしてから Tool を呼ぶことがある。
    ここで Tool 呼び出しを無視すると、前置きがそのまま最終回答として
    確定し、本当の回答が返らない。流し終えた本文は呼び出し側が
    `answer_reset` で取り消す。
    """
    round_ = _StreamedRound()
    truncated = False
    chunks = chat_completion_stream(
        conversation,
        tools=tools,
        temperature=TEMPERATURE,
        max_tokens=MAX_ANSWER_TOKENS,
    )
    # 途中で捨てる場合に vLLM 側の生成を止めるため、必ず閉じる
    with closing(chunks):
        for chunk in chunks:
            totals.add(chunk.prompt_tokens, chunk.completion_tokens)
            if chunk.has_tool_calls:
                round_.saw_tool_calls = True
                break
            if chunk.finish_reason == "length":
                truncated = True
            if chunk.delta:
                round_.text += chunk.delta
                yield ChatStreamTextEvent(delta=chunk.delta)

    if truncated:
        round_.text += TRUNCATED_SUFFIX
        yield ChatStreamTextEvent(delta=TRUNCATED_SUFFIX)
    return round_


def _event_arguments(name: str, raw: Any, top_k: int) -> dict[str, Any]:
    """`tool_call` イベントに載せる引数。表示専用。

    実行時の検証は execute_agent_tool が行うため、ここでは壊れていても
    落とさずに空で流す。進捗表示のために回答全体を落とすのは割に合わない。
    検索件数だけ実行側の上書き（`_force_top_k`）と表示を揃える。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {**raw, "top_k": top_k} if name == SEARCH_KNOWLEDGE else raw


def _citation_fingerprint(drafts: dict[UUID, _CitationDraft]) -> tuple[tuple[UUID, int], ...]:
    """出典が増えたときにだけ流すための目印。

    `citations` は毎回全件を送る契約なので、変化していないのに送ると
    同じ配列を何度も往復させることになる。
    """
    return tuple((draft.knowledge_id, len(draft.utterances)) for draft in drafts.values())
