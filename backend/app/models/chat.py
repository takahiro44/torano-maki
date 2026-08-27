"""AIチャットの入出力契約。

**このファイルがフロントエンドとの契約の正になる。**
FastAPI が OpenAPI スキーマを生成するので、フロント側の型
（`frontend/src/types/api.ts`）はここから起こすこと（CLAUDE.md 5章）。

会話履歴をサーバに保存しない。認証・ユーザー管理を作らない方針
（CLAUDE.md 3.1）のため会話の所有者を定義できず、保存すると
誰の会話かを判定できないまま溜まっていくため。
そのかわり**クライアントが毎回すべての履歴を送る**。
将来DBに持たせる場合も `conversation_id` を足すだけで移行できる。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatRole(StrEnum):
    """クライアントが送れる役割。

    `system` を含めないのは、指示文をクライアントから差し替えられると
    Tool の使い方や出典の扱いを壊せてしまうため。system はサーバが付ける。
    `tool` を含めないのは、Tool の実行結果はサーバ内で完結し、
    クライアントが偽の実行結果を注入できてはいけないため。
    """

    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ChatRole
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(
        min_length=1,
        max_length=40,
        description="会話履歴。**毎回すべて送ること。**最後の要素が今回の質問",
    )
    top_k: int = Field(
        default=5, ge=1, le=20, description="Agentが検索するときのKnowledge件数の上限"
    )


class CitationUtterance(BaseModel):
    """出典として示す1発話。"""

    sequence_no: int
    speaker: str
    start_sec: float
    end_sec: float
    content: str


class Citation(BaseModel):
    """Agentが参照したナレッジ。

    **本文からは組み立てない。実際に実行した Tool の結果からのみ作る。**
    LLM に出典を書かせると、それらしいIDやタイトルを捏造する。
    利用者が検証できないと信頼できないため（CLAUDE.md 6章）。

    **「回答が引用したもの」ではなく「Agentが見たもの」。**
    検索は当たったが回答が「該当なし」になる場合も、ヒットしたナレッジが入る。
    どれを引用したかはモデルにしか分からず、書かせると捏造するため、
    こちらでは絞り込めない。画面では「回答の根拠」ではなく
    「AIが参照した情報」として見せること。
    """

    knowledge_id: UUID
    title: str
    data_source_id: UUID | None = None
    source_type: str | None = Field(default=None, description="audio / manual など")
    file_name: str | None = Field(default=None, description="音声由来の場合のファイル名")
    utterances: list[CitationUtterance] = Field(
        default_factory=list,
        description="根拠の発話。Agentが get_knowledge_evidence を呼んだ場合のみ入る",
    )


class ToolTraceStep(BaseModel):
    """Agentが実行したTool 1回分。

    **回答が返るまで10秒以上かかる。** 無言で待たせないための材料と、
    「本当にDBを見たのか」を利用者と開発者が確認するための記録。
    非ストリーミングでは回答と同時に届くため、進捗表示ではなく
    事後の根拠表示・デバッグに使う。
    """

    step: int = Field(description="1から始まる実行順")
    tool: str
    ok: bool
    summary: str = Field(description="人が読める1行の結果。画面にそのまま出せる")
    error_code: str | None = Field(default=None, description="失敗時のみ。ok=false と対応する")


class ChatUsage(BaseModel):
    iterations: int = Field(description="LLMへの往復回数")
    prompt_tokens: int = 0
    completion_tokens: int = 0
    hit_max_iterations: bool = Field(
        default=False,
        description="上限に達して打ち切ったか。true なら回答が不完全な可能性がある",
    )


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(
        default_factory=list, description="Agentが参照したナレッジ。引用元とは限らない"
    )
    tool_trace: list[ToolTraceStep] = Field(default_factory=list)
    usage: ChatUsage


# --- ストリーミング（POST /chat/stream）---
#
# 非ストリーミングの ChatResponse とは**別の契約**として足す。
# 既存の定義には手を入れない（CLAUDE.md 1.1 の共有ファイルのため）。
#
# 1イベント = SSE の `data: <JSON>\n\n` 1件。
# `event:` フィールドを使わず JSON の `type` で判別する形にしているのは、
# フロントが型ごとに addEventListener を生やさずに済むようにするため。


class ChatStreamErrorCode(StrEnum):
    """`error` イベントの分類。

    ストリームは接続確立時点で 200 が返るため、そのあとの失敗を
    ステータスコードで表現できない。画面が「設定漏れ」「DGXが落ちている」
    「想定外」を出し分けられるように、コードとして流す。
    """

    LLM_NOT_CONFIGURED = "llm_not_configured"
    LLM_UNREACHABLE = "llm_unreachable"
    INTERNAL = "internal"


class ChatStreamToolCallEvent(BaseModel):
    """Tool を実行する直前に出す。

    検索に数秒かかる間、画面を無言にしないための材料。
    `label` を**サーバが日本語で決める**のは、Tool が増えたときに
    フロントの対応表を直さずに済ませるため。
    """

    type: Literal["tool_call"] = "tool_call"
    step: int = Field(description="1から始まる実行順。tool_result と対応する")
    tool: str
    label: str = Field(description="画面にそのまま出せる日本語。例『ナレッジを検索しています』")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="実際に渡した引数。壊れていた場合は空"
    )


class ChatStreamToolResultEvent(BaseModel):
    """Tool の実行後に出す。中身は既存 ToolTraceStep と同じ。"""

    type: Literal["tool_result"] = "tool_result"
    step: int
    tool: str
    ok: bool
    summary: str = Field(description="人が読める1行。ToolTraceStep.summary と同じ文字列")
    error_code: str | None = None


class ChatStreamCitationsEvent(BaseModel):
    """出典が増えたときに出す。

    **差分ではなく毎回すべてを送る。** フロントは置き換えるだけでよく、
    途中のイベントを1つ取りこぼしても表示が壊れない。
    """

    type: Literal["citations"] = "citations"
    citations: list[Citation] = Field(default_factory=list)


class ChatStreamTextEvent(BaseModel):
    """最終回答のトークン。

    **Tool 呼び出し中のラウンドの出力は流さない。**
    体感待ち時間を縮めるのがこのイベントの唯一の目的で、
    途中の思考を見せると読み手が回答と取り違える。
    """

    type: Literal["text"] = "text"
    delta: str


class ChatStreamAnswerResetEvent(BaseModel):
    """ここまで流した `text` を破棄させる。

    **最終回答のラウンドかどうかは、投げてみるまで分からない。**
    Agent は「根拠の発言を確認します」のような前置きを書いてから
    `tool_calls` を出すことがある。前置きを流し終えるまで Tool 呼び出しは
    判明しないため、取り消す手段が無いと前置きが回答として確定してしまう
    （実際、モデルが要求した Tool を捨てて前置きだけを回答にする不具合が出た）。

    取り消せるようにしておけば、Agent の判断を曲げずに済む。
    """

    type: Literal["answer_reset"] = "answer_reset"
    reason: Literal["tool_call"] = Field(
        default="tool_call", description="破棄の理由。今は Tool 呼び出しの検出のみ"
    )


class ChatStreamDoneEvent(BaseModel):
    """正常終了。途中経過と食い違ったらこちらが確定値。"""

    type: Literal["done"] = "done"
    usage: ChatUsage


class ChatStreamErrorEvent(BaseModel):
    """異常終了。これ以降イベントは来ない。"""

    type: Literal["error"] = "error"
    code: ChatStreamErrorCode
    message: str


ChatStreamEvent = Annotated[
    ChatStreamToolCallEvent
    | ChatStreamToolResultEvent
    | ChatStreamCitationsEvent
    | ChatStreamTextEvent
    | ChatStreamAnswerResetEvent
    | ChatStreamDoneEvent
    | ChatStreamErrorEvent,
    Field(discriminator="type"),
]


# --- 音声入力（POST /chat/voice）---
#
# 話して質問するための文字起こし。**チャットの契約とは独立させる。**
# 文字起こしの結果は入力欄に入るだけで、送信するかどうかは人が決めるため、
# ChatRequest とは別のやりとりになる（理由は api/chat.py の transcribe_question）。


class ChatTranscription(BaseModel):
    """話した質問の文字起こし結果。

    **`data_source_id` を返さない。** 質問はナレッジの出典ではなく保存もしないため、
    返せるIDが存在しない。`AudioTranscribeResponse` と形を揃えなかったのは、
    無いIDを `null` で埋めるとフロントが「取れなかった」と誤読するため。
    """

    text: str
    language: str | None = None
    duration_sec: float = Field(default=0.0, description="話した長さ。区間が取れなかった場合は 0")
