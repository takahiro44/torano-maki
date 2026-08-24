"""CBR / 抽出スキーマ。定義の正は `knowledge.py`。配置指示に合わせた再エクスポート。"""

from app.models.knowledge import (
    ExtractedKnowledge,
    ExtractionResult,
    StructuredData,
)
from app.models.knowledge import (
    Knowledge as KnowledgeResponse,
)

__all__ = [
    "ExtractedKnowledge",
    "ExtractionResult",
    "KnowledgeResponse",
    "StructuredData",
]
