"""ナレッジのスキーマ定義。**このファイルがスキーマの唯一の源。**

CBR ケース構造 (Aamodt & Plaza 1994) を LLM・API・DB列で共有する。
DDL の正は `docker/initdb/02_schema.sql`。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
)


class KnowledgeStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class SourceType(StrEnum):
    """data_sources.source_type。"""

    AUDIO = "audio"
    DOCUMENT = "document"
    MANUAL = "manual"
    ROLEPLAY = "roleplay"
    INTERVIEW = "interview"


NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
TitleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class StructuredData(BaseModel):
    """CBR ケース構造。LLM 抽出結果のバリデーション用。"""

    model_config = ConfigDict(extra="forbid")

    situation: str | None = Field(None, description="状況: 何が起きたか")
    customer_issue: str | None = Field(None, description="顧客課題: 何が障壁だったか")
    sales_action: str | None = Field(None, description="営業対応: 何をどう判断し実行したか")
    action_reason: str | None = Field(None, description="対応理由: なぜその行動を選んだか")
    result: str | None = Field(None, description="結果: どうなったか")
    learning: str | None = Field(None, description="学び: 抽象化された教訓")


class ExtractedKnowledge(BaseModel):
    """LLM が原文から抽出した 1 件。プロンプトはフラット JSON。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=100)
    situation: str | None = None
    customer_issue: str | None = None
    sales_action: str | None = None
    action_reason: str | None = None
    result: str | None = None
    learning: str | None = None
    knowledge_type: str = "sales_knowhow"

    @field_validator("title", mode="before")
    @classmethod
    def _clip_title(cls, v: object) -> object:
        if isinstance(v, str) and len(v) > 100:
            return v[:100]
        return v

    @property
    def structured_data(self) -> StructuredData:
        return StructuredData(
            situation=self.situation,
            customer_issue=self.customer_issue,
            sales_action=self.sales_action,
            action_reason=self.action_reason,
            result=self.result,
            learning=self.learning,
        )


# 後方互換。抽出テストやプレビューが ExtractedItem を参照していたため。
ExtractedItem = ExtractedKnowledge

CBR_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("title", "タイトル"),
    ("situation", "状況"),
    ("customer_issue", "顧客課題"),
    ("sales_action", "営業対応"),
    ("action_reason", "対応理由"),
    ("result", "結果"),
    ("learning", "学び"),
)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExtractedKnowledge] = Field(default_factory=list)


class KnowledgeCreate(BaseModel):
    """手入力登録。title があればよい。CBR は任意。"""

    title: TitleText
    knowledge_type: str = "sales_knowhow"
    situation: str | None = None
    customer_issue: str | None = None
    sales_action: str | None = None
    action_reason: str | None = None
    result: str | None = None
    learning: str | None = None
    original_content: str | None = None
    data_source_id: UUID | None = None
    status: KnowledgeStatus = KnowledgeStatus.CONFIRMED


class KnowledgeUpdate(BaseModel):
    title: TitleText | None = None
    situation: str | None = None
    customer_issue: str | None = None
    sales_action: str | None = None
    action_reason: str | None = None
    result: str | None = None
    learning: str | None = None
    status: KnowledgeStatus | None = None

    @field_validator(
        "title",
        "situation",
        "customer_issue",
        "sales_action",
        "action_reason",
        "result",
        "learning",
        "status",
        mode="before",
    )
    @classmethod
    def _reject_explicit_null(cls, v: object) -> object:
        if v is None:
            raise ValueError("null は指定できません。変更しない項目はキーごと省略してください")
        return v


class KnowledgeStatusPatch(BaseModel):
    status: KnowledgeStatus


class Knowledge(BaseModel):
    """APIが返す Knowledge。search_text / embedding は出さない。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    data_source_id: UUID | None = None
    knowledge_type: str
    title: str
    situation: str | None = None
    customer_issue: str | None = None
    sales_action: str | None = None
    action_reason: str | None = None
    result: str | None = None
    learning: str | None = None
    original_content: str | None = None
    status: KnowledgeStatus
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def source_id(self) -> UUID | None:
        """出典。CLAUDE.md 6章。data_source_id と同じ。"""
        return self.data_source_id

    @computed_field
    @property
    def source_type(self) -> str:
        return "manual"

    @computed_field
    @property
    def content(self) -> str:
        """一覧・検索の互換表示。CBR をフラット化したもの。"""
        from app.services.search_text import generate_search_text

        return generate_search_text(
            title=self.title,
            situation=self.situation,
            customer_issue=self.customer_issue,
            sales_action=self.sales_action,
            action_reason=self.action_reason,
            result=self.result,
            learning=self.learning,
        )


class KnowledgeSearchResult(Knowledge):
    score: float = Field(description="コサイン類似度。1に近いほど query に近い")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, description="自然文の検索クエリ")
    top_k: int = Field(default=5, ge=1, le=50)


class ExtractRequest(BaseModel):
    text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000),
    ]
    data_source_id: UUID | None = None


class IngestTextRequest(BaseModel):
    raw_text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000),
    ]
    created_by: str | None = None
    data_source_id: UUID | None = None


class IngestPreviewItem(ExtractedKnowledge):
    content: str


class IngestTextResponse(BaseModel):
    raw_text: str
    extracted: list[IngestPreviewItem]
    saved: list[Knowledge] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
