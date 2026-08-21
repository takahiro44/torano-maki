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
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


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


# 前後の空白を落としたうえで1文字以上を要求する。
# min_length だけだと "   " のような空白だけの本文が通ってしまい、
# APIを直接叩かれたときに中身の無いナレッジが登録される。
NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class KnowledgeCreate(BaseModel):
    """Knowledge の登録リクエスト。

    MVPでは content だけあれば登録できる。
    他の項目はAI整理・議事録・音声を追加する段階で使う。
    """

    content: NonBlankText = Field(description="ナレッジ本文。検索対象になる")
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
    """Knowledge の更新リクエスト。指定した項目だけ更新する。

    「変更しない」はキーを省略して表す。**明示的な null は受け付けない。**
    null を許すと、`exclude_unset` では省略と区別できないまま None が
    DBの NOT NULL 列へ渡り、500 になるため。
    """

    content: NonBlankText | None = None
    status: KnowledgeStatus | None = None

    @field_validator("content", "status", mode="before")
    @classmethod
    def _reject_explicit_null(cls, v: object) -> object:
        """明示的な null を 422 にする。

        既定値の None には走らない（バリデータは値が渡されたときだけ動く）ため、
        キーを省略した場合は従来どおり「変更しない」になる。
        """
        if v is None:
            raise ValueError("null は指定できません。変更しない項目はキーごと省略してください")
        return v


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
