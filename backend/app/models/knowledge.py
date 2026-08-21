"""ナレッジのスキーマ定義。**このファイルがスキーマの唯一の源。**

ここで定義したPydanticモデルが、
  - LLMに渡すJSON Schema（構造化出力の指定）
  - DBのテーブル構造（tables.py がこれに対応する）
  - APIのレスポンス型（フロントの型もここから生成される）
を兼ねる（CLAUDE.md 6章）。3箇所に別々の定義を書かないこと。

このファイルは全員が依存するため、変更するときは必ずチームに共有すること。
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# =============================================================================
# TODO: ナレッジの構造はチームで設計して確定させる。
#       以下は import を通すための仮置きであり、確定版ではない。
#       「何を1件のナレッジとするか」は本プロダクトの中心的な設計判断なので、
#       担当者が設計し、決定の理由を docs/decisions.md に残すこと。
# =============================================================================


class KnowledgeBase(BaseModel):
    """1件のナレッジ。仮の構造。"""

    title: str = Field(description="ナレッジの見出し")
    situation: str = Field(description="どんな状況で使えるか")
    action: str = Field(description="何をしたか")
    outcome: str = Field(description="結果どうなったか")
    tags: list[str] = Field(default_factory=list)


class KnowledgeCreate(KnowledgeBase):
    """蓄積時の入力。出典を必須にする。

    出典がないナレッジは検証できず信頼できないため、
    生成元を必ず紐づける（CLAUDE.md 6章）。
    """

    source_id: UUID


class Knowledge(KnowledgeBase):
    """APIが返すナレッジ。検索結果には必ず出典を含める。"""

    id: UUID
    source_id: UUID
    created_at: datetime
