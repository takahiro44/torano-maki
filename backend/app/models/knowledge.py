"""ナレッジのスキーマ定義。**このファイルがスキーマの唯一の源。**

ここで定義したPydanticモデルが、
  - LLMに渡すJSON Schema（構造化出力の指定）
  - APIのリクエスト・レスポンス型
  - フロントの型（OpenAPIスキーマ経由）
を兼ねる（CLAUDE.md 6章）。3箇所に別々の定義を書かないこと。

DBのテーブル定義は `tables.py`、実際のDDLは `docker/initdb/02_schema.sql`。

設計の要点:
- **入力時に構造化を強制しない。** 雑なテキストをそのまま受け取り、
  分類や整理は後から行う（実装計画 §4）。そのため必須項目は content だけ
- **元データを失わない。** AI整理を入れる段階では original_content に
  整理前のテキストを残す。AIの整理が間違っていても復元できる（実装計画 §6）
- **出典を辿れるようにする。** source_type / source_id で、どの入力から
  生まれたKnowledgeかを記録する（CLAUDE.md 6章）
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeStatus(StrEnum):
    """人間による確認が済んでいるか。

    AIが抽出した候補を、人間が確認してから正式なKnowledgeにするため
    （実装計画 §22「AIは提案、人間が最終判断」）。
    手入力のものは確認済みとして直接 CONFIRMED で登録してよい。
    """

    DRAFT = "draft"  # AIが抽出した候補。まだ検索対象にしない
    CONFIRMED = "confirmed"  # 人間が確認済み。検索対象
    REJECTED = "rejected"  # 人間が却下。残すのは再学習・分析のため


class SourceType(StrEnum):
    """どの入力経路から来たか。

    入力方法が増えてもKnowledge側を変えずに済むよう、
    経路を値で持つ（実装計画 §7 Input Adapter）。
    """

    MANUAL = "manual"  # 自由入力
    MEETING = "meeting"  # 議事録
    AUDIO = "audio"  # 音声


class KnowledgeCreate(BaseModel):
    """Knowledge の登録リクエスト。

    MVPでは content だけあれば登録できる。
    他の項目はAI整理・議事録・音声を追加する段階で使う。
    """

    content: str = Field(min_length=1, description="ナレッジ本文。検索対象になる")
    original_content: str | None = Field(
        default=None,
        description="AI整理前の元テキスト。手入力の場合はNone",
    )
    source_type: SourceType = SourceType.MANUAL
    source_id: UUID | None = Field(
        default=None,
        description="元になった入力（議事録・音声など）のID。将来用",
    )
    created_by: str | None = Field(default=None, description="登録者。認証は作らないため任意")
    status: KnowledgeStatus = KnowledgeStatus.CONFIRMED


class KnowledgeUpdate(BaseModel):
    """Knowledge の更新リクエスト。指定した項目だけ更新する。"""

    content: str | None = Field(default=None, min_length=1)
    status: KnowledgeStatus | None = None


class Knowledge(BaseModel):
    """APIが返す Knowledge。

    embedding は返さない。1024個の浮動小数点数はフロントで使い道がなく、
    レスポンスを無駄に重くするため。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content: str
    original_content: str | None
    status: KnowledgeStatus
    source_type: SourceType
    source_id: UUID | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeSearchResult(Knowledge):
    """検索結果。類似度を添えて返す。

    どのKnowledgeがどれだけ近かったかを示さないと、
    利用者が結果を信頼できないため（CLAUDE.md 6章）。
    """

    score: float = Field(description="コサイン類似度。1に近いほど query に近い")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, description="自然文の検索クエリ")
    top_k: int = Field(default=5, ge=1, le=50)
