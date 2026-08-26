"""上司レビュー機能の入出力契約。

AIチャットの会話ログを上司に確認してもらい、回答を新しいナレッジとして
還元するための一連のAPI契約。チャット自体はサーバに永続化しない（chat.py参照）ため、
上司に見せる文脈は `chat_reviews.chat_history` にスナップショットして保持する。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.chat import ChatMessage


class ChatReviewSummary(BaseModel):
    """LLMへの構造化出力契約。「まとめる」の中身そのもの。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="会話全体の要約")
    understood_points: list[str] = Field(
        default_factory=list, description="後輩が理解できていた事項"
    )
    knowledge_gaps: list[str] = Field(
        default_factory=list, description="蓄積ナレッジだけでは埋まらなかった疑問点"
    )


class SummarizeChatReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class ChatReviewListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    summary: str
    status: str
    created_at: datetime
    answered_at: datetime | None = None


class CreatedKnowledgeItem(BaseModel):
    id: UUID
    title: str


class ChatReviewDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chat_history: list[ChatMessage]
    summary: str
    understood_points: list[str]
    knowledge_gaps: list[str]
    status: str
    supervisor_response: str | None = None
    answered_data_source_id: UUID | None = None
    created_at: datetime
    answered_at: datetime | None = None
    created_knowledge: list[CreatedKnowledgeItem] = Field(default_factory=list)


class RespondChatReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_text: str = Field(min_length=1, max_length=20_000)
