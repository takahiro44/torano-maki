"""文字起こしからナレッジを抽出する際の入出力契約。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """未定義項目を拒否し、LLM出力と保存構造のずれを早期検出する。"""

    model_config = ConfigDict(extra="forbid")


class TranscriptSegment(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> TranscriptSegment:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class TranscriptDocument(BaseModel):
    """文字起こし実験のJSON。metaなど検証固有の情報は読み飛ばす。"""

    model_config = ConfigDict(extra="ignore")

    text: str
    segments: list[TranscriptSegment] = Field(min_length=1)
    language: str | None = None


class SpeakerAssignment(StrictModel):
    sequence_no: int = Field(ge=1)
    speaker: Literal["salesperson", "customer", "unknown"]


class EvidenceSpan(StrictModel):
    """LLMにはUUIDでなく、入力に実在する連番だけを選ばせる。"""

    start_sequence_no: int = Field(ge=1)
    end_sequence_no: int = Field(ge=1)


class KnowledgeDraft(StrictModel):
    knowledge_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
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
    evidence: list[EvidenceSpan] = Field(min_length=1)


class CallSummaryDraft(StrictModel):
    summary: str = Field(min_length=1)
    customer_needs: list[str]
    proposals: list[str]
    decisions: list[str]
    next_actions: list[str]


class LlmExtraction(StrictModel):
    """DGXのLLMに生成させる中間構造。DBのUUIDは含めない。"""

    speaker_assignments: list[SpeakerAssignment] = Field(min_length=1)
    knowledge_units: list[KnowledgeDraft] = Field(min_length=1)
    call_summary: CallSummaryDraft


class DataSource(StrictModel):
    id: UUID
    source_type: str
    file_name: str
    occurred_at: datetime


class UtteranceSegment(StrictModel):
    id: UUID
    data_source_id: UUID
    sequence_no: int
    speaker: str
    start_sec: float
    end_sec: float
    content: str


class KnowledgeUnit(StrictModel):
    id: UUID
    data_source_id: UUID
    knowledge_type: str
    title: str
    situation: str | None
    problem: str | None
    judgment: str | None
    action: str | None
    reasoning: str | None
    outcome: str | None
    lesson: str | None
    applicable_situations: str | None
    limitations: str | None
    industry: str | None
    product: str | None
    sales_stage: str | None
    search_text: str
    embedding: list[float] | None
    embedding_model: str | None
    created_at: datetime


class KnowledgeEvidence(StrictModel):
    id: UUID
    knowledge_id: UUID
    start_utterance_id: UUID
    end_utterance_id: UUID


class CallSummary(StrictModel):
    id: UUID
    data_source_id: UUID
    summary: str
    customer_needs: list[str]
    proposals: list[str]
    decisions: list[str]
    next_actions: list[str]


class ExperimentResult(StrictModel):
    """ER図の5テーブルへ投入できる形に整えた検証結果。"""

    data_sources: list[DataSource]
    utterance_segments: list[UtteranceSegment]
    knowledge_units: list[KnowledgeUnit]
    knowledge_evidence: list[KnowledgeEvidence]
    call_summaries: list[CallSummary]
