"""ナレッジのスキーマ定義。**このファイルがスキーマの唯一の源。**

検証済み ER（docs/knowledge-extraction-design.md）の knowledge_units と揃える。
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


class KnowledgeSortField(StrEnum):
    """一覧の並べ替え。ホワイトリスト用。"""

    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    TITLE = "title"
    STATUS = "status"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class SourceType(StrEnum):
    AUDIO = "audio"
    DOCUMENT = "document"
    MANUAL = "manual"
    ROLEPLAY = "roleplay"
    INTERVIEW = "interview"


TitleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

_SEARCH_FIELD_NAMES = (
    "title",
    "situation",
    "problem",
    "judgment",
    "action",
    "reasoning",
    "outcome",
    "lesson",
    "applicable_situations",
    "limitations",
    "industry",
    "product",
    "sales_stage",
)


class StructuredData(BaseModel):
    """CBR + 適用条件。LLM 抽出結果のバリデーション用。"""

    model_config = ConfigDict(extra="forbid")

    situation: str | None = None
    problem: str | None = None
    judgment: str | None = None
    action: str | None = None
    reasoning: str | None = None
    outcome: str | None = None
    lesson: str | None = None
    applicable_situations: str | None = None
    limitations: str | None = None


class ExtractedKnowledge(BaseModel):
    """LLM が原文から抽出した 1 件。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=100)
    situation: str | None = None
    problem: str | None = None
    judgment: str | None = None
    action: str | None = None
    reasoning: str | None = None
    outcome: str | None = None
    lesson: str | None = None
    applicable_situations: str | None = None
    limitations: str | None = None
    industry: str | None = None
    product: str | None = None
    sales_stage: str | None = None
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
            problem=self.problem,
            judgment=self.judgment,
            action=self.action,
            reasoning=self.reasoning,
            outcome=self.outcome,
            lesson=self.lesson,
            applicable_situations=self.applicable_situations,
            limitations=self.limitations,
        )


ExtractedItem = ExtractedKnowledge

CBR_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("title", "タイトル"),
    ("situation", "状況"),
    ("problem", "顧客課題"),
    ("judgment", "判断"),
    ("action", "行動"),
    ("reasoning", "理由"),
    ("outcome", "結果"),
    ("lesson", "学び"),
    ("applicable_situations", "適用場面"),
    ("limitations", "制約・非適用"),
    ("industry", "業界"),
    ("product", "商材"),
    ("sales_stage", "商談フェーズ"),
)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExtractedKnowledge] = Field(default_factory=list)


class CallSummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    customer_needs: list[str] = Field(default_factory=list)
    proposals: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class KnowledgeCreate(BaseModel):
    """手入力登録。title があればよい。"""

    title: TitleText
    knowledge_type: str = "sales_knowhow"
    situation: str | None = None
    problem: str | None = None
    judgment: str | None = None
    action: str | None = None
    reasoning: str | None = None
    outcome: str | None = None
    lesson: str | None = None
    applicable_situations: str | None = None
    limitations: str | None = None
    industry: str | None = None
    product: str | None = None
    sales_stage: str | None = None
    data_source_id: UUID | None = None
    status: KnowledgeStatus = KnowledgeStatus.CONFIRMED


class KnowledgeUpdate(BaseModel):
    title: TitleText | None = None
    situation: str | None = None
    problem: str | None = None
    judgment: str | None = None
    action: str | None = None
    reasoning: str | None = None
    outcome: str | None = None
    lesson: str | None = None
    applicable_situations: str | None = None
    limitations: str | None = None
    industry: str | None = None
    product: str | None = None
    sales_stage: str | None = None
    status: KnowledgeStatus | None = None

    @field_validator(*_SEARCH_FIELD_NAMES, "status", mode="before")
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
    problem: str | None = None
    judgment: str | None = None
    action: str | None = None
    reasoning: str | None = None
    outcome: str | None = None
    lesson: str | None = None
    applicable_situations: str | None = None
    limitations: str | None = None
    industry: str | None = None
    product: str | None = None
    sales_stage: str | None = None
    embedding_model: str | None = None
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
        from app.services.search_text import generate_search_text_from_mapping

        return generate_search_text_from_mapping(
            self.model_dump(exclude={"content", "source_id", "source_type"})
        )


class KnowledgeSearchResult(Knowledge):
    """ハイブリッド検索の1件。

    `score` は RRF スコア。**方式をまたいで比較できる絶対値ではない**（0.03 程度の
    小さな値になる）。順位を決めるための内部値であり、そのまま画面に出す想定ではない。
    内訳を別に返しているのは、なぜその順位になったかを追えるようにするため。
    """

    score: float = Field(description="RRFスコア。順位の根拠であり、類似度ではない")
    semantic_score: float | None = Field(
        default=None, description="コサイン類似度。ベクトル検索で拾えなかった場合は null"
    )
    lexical_score: float | None = Field(
        default=None, description="pg_trgm の word_similarity。語が一致しなければ null"
    )
    semantic_rank: int | None = Field(default=None, description="ベクトル検索内での順位")
    lexical_rank: int | None = Field(default=None, description="語彙検索内での順位")


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


class CallSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    data_source_id: UUID
    summary: str
    customer_needs: list[str]
    proposals: list[str]
    decisions: list[str]
    next_actions: list[str]
    created_at: datetime


class UtteranceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sequence_no: int
    speaker: str
    start_sec: float
    end_sec: float
    content: str


class KnowledgeEvidenceSpan(BaseModel):
    """根拠として紐づく発話。start〜end の連番を含む。"""

    start_sequence_no: int
    end_sequence_no: int
    utterances: list[UtteranceOut]


class GenerateSummaryRequest(BaseModel):
    data_source_id: UUID
