"""上司レビュー機能の入出力契約。

AIチャットの会話ログを上司に確認してもらい、回答を新しいナレッジとして
還元するための一連のAPI契約。チャット自体はサーバに永続化しない（chat.py参照）ため、
上司に見せる文脈は `chat_reviews.chat_history` にスナップショットして保持する。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.chat import ChatMessage, ChatStreamErrorCode


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


# --- 実況つきの「まとめる」（docs/plan-interactive-coaching.md 機能A） ---
#
# **なぜ既存の ChatReviewSummary を変えないか。** 3値の理解度マップ（3.2）を
# 入れると保存済みの行が新スキーマで落ち、DB作り直しが要る（6章）。
# ここで足しているのは「サーバがナレッジDBを照合した結果」だけで、
# 保存する形は変えていないため、DDLもDB作り直しも要らない。


class GapDbState(StrEnum):
    """その疑問に対するナレッジDBの状態。

    **Qwen に決めさせない。** サーバが実際に検索した結果から埋める
    （agent_loop.py の「出典は Tool の結果からのみ作る」と同じ理由）。

    `thin`（当たるが薄い）は入れていない。判定には会話中の citations が要るが、
    `chat_reviews.chat_history` は role / content しか持たない（計画 3.3）。
    """

    MISSING = "missing"
    FOUND_BUT_UNREACHABLE = "found_but_unreachable"


class GapKnowledgeHit(BaseModel):
    """照合で当たった既存ナレッジ1件。

    `semantic_score` を出すのは、上司が判定を検証できるようにするため。
    **RRF の `score` は載せない**（models/knowledge.py:369 のとおり、
    順位のための内部値であり類似度ではない）。
    """

    model_config = ConfigDict(extra="forbid")

    knowledge_id: UUID
    title: str
    semantic_score: float | None = None


class GapDiagnosis(BaseModel):
    """疑問点1つと、それに対するナレッジDBの状態。"""

    model_config = ConfigDict(extra="forbid")

    gap: str
    db_state: GapDbState
    existing_knowledge: list[GapKnowledgeHit] = Field(default_factory=list)


class ChatReviewDiagnosis(BaseModel):
    """実況つき「まとめる」の最終成果。`done` イベントに載る。"""

    model_config = ConfigDict(extra="forbid")

    summary: str
    understood_points: list[str] = Field(default_factory=list)
    gaps: list[GapDiagnosis] = Field(default_factory=list)


class ReviewStreamStepEvent(BaseModel):
    """処理を始める直前に出す。

    **チャットの `tool_call` と役割は同じだが、名前を変えている。**
    こちらは LLM が選んだ Tool ではなく、サーバが順に踏む工程である。
    tool と呼ぶと、モデルが判断したかのように読めてしまう。
    """

    type: Literal["step"] = "step"
    step: int = Field(description="1から始まる実行順。step_result と対応する")
    label: str = Field(description="画面にそのまま出せる日本語")


class ReviewStreamStepResultEvent(BaseModel):
    """工程の完了後に出す。`summary` はそのまま1行として画面に出せる。"""

    type: Literal["step_result"] = "step_result"
    step: int
    ok: bool
    summary: str
    error_code: str | None = None


class ReviewStreamDoneEvent(BaseModel):
    """正常終了。途中経過と食い違ったら、こちらを確定値として採用する。"""

    type: Literal["done"] = "done"
    diagnosis: ChatReviewDiagnosis


class ReviewStreamErrorEvent(BaseModel):
    """異常終了。以降イベントは来ない。"""

    type: Literal["error"] = "error"
    code: ChatStreamErrorCode
    message: str


ReviewStreamEvent = (
    ReviewStreamStepEvent
    | ReviewStreamStepResultEvent
    | ReviewStreamDoneEvent
    | ReviewStreamErrorEvent
)
