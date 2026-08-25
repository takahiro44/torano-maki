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
