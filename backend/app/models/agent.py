"""Agent Tool の入出力契約。

Qwen に公開する Tool の引数はこのファイルの Pydantic モデルから
JSON Schema を生成する。検索対象は Knowledge のみに限定し、Evidence・
Summary・Utterance は ID / FK で取得する。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeSearchFilters(BaseModel):
    """Knowledge の構造化列に対する完全一致フィルタ。"""

    model_config = ConfigDict(extra="forbid")

    industry: str | None = Field(default=None, description="業界の完全一致条件")
    product: str | None = Field(default=None, description="商材の完全一致条件")
    sales_stage: str | None = Field(default=None, description="商談フェーズの完全一致条件")
    knowledge_type: str | None = Field(default=None, description="ナレッジ種別の完全一致条件")


class SearchKnowledgeToolArgs(BaseModel):
    """Knowledge を一次検索する Tool。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2_000, description="ユーザーの質問または検索文")
    top_k: int = Field(default=5, ge=1, le=20, description="返すKnowledgeの最大件数")
    filters: KnowledgeSearchFilters | None = Field(
        default=None,
        description="必要な場合だけ指定する構造化フィルタ",
    )


class GetKnowledgeEvidenceToolArgs(BaseModel):
    """Knowledge に紐づく根拠範囲を取得する Tool。"""

    model_config = ConfigDict(extra="forbid")

    knowledge_id: UUID = Field(description="検索結果に含まれるKnowledge ID")


class GetCallSummaryToolArgs(BaseModel):
    """Knowledge と同じ入力元の商談要約を取得する Tool。"""

    model_config = ConfigDict(extra="forbid")

    data_source_id: UUID = Field(description="Knowledgeに含まれるdata_source_id")


class GetUtteranceContextToolArgs(BaseModel):
    """1発言またはEvidence範囲の前後文脈を取得する Tool。"""

    model_config = ConfigDict(extra="forbid")

    start_utterance_id: UUID = Field(description="単一発言の場合は対象ID、範囲の場合は開始発言ID")
    end_utterance_id: UUID | None = Field(
        default=None,
        description="範囲の終了発言ID。省略時は開始発言だけを対象にする",
    )
    before: int = Field(default=2, ge=0, le=10, description="開始発言より前に追加する発言数")
    after: int = Field(default=2, ge=0, le=10, description="終了発言より後に追加する発言数")
