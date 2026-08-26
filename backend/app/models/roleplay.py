"""ロープレの入出力契約。

**このファイルがフロントエンドとの契約の正になる。**
FastAPI が OpenAPI スキーマを生成するので、フロント側の型
（`frontend/src/types/api.ts`）はここから起こすこと（CLAUDE.md 5章）。

**Qwen に出させてよいものと、サーバが付けるものを型で分けている。**
`RoleplayScenario` は Qwen の出力契約で、Knowledge ID も Evidence ID も
持たない。出典はサーバが実際に渡したものだけから組み立てる
（LLM に出典を書かせると捏造するため。CLAUDE.md 6章）。

認証を作らない方針（CLAUDE.md 3.1）のため、練習履歴に社員IDを持たせない。
匿名の練習記録として扱う。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoleplayCategory(StrEnum):
    """練習できる場面。

    商談全体ではなく「判断が必要な一場面」だけを反復するため、
    値引き・反対意見のような**分岐点**を単位にしている。
    """

    NEEDS_DISCOVERY = "needs_discovery"
    PRICE_OBJECTION = "price_objection"
    OBJECTION = "objection"
    COMPLAINT = "complaint"
    NEXT_COMMITMENT = "next_commitment"


# 画面に出す名前。フロントで同じ対応表を書くとズレるため、ここを正にする。
CATEGORY_LABELS: dict[RoleplayCategory, str] = {
    RoleplayCategory.NEEDS_DISCOVERY: "課題深掘り",
    RoleplayCategory.PRICE_OBJECTION: "値引き",
    RoleplayCategory.OBJECTION: "反対意見",
    RoleplayCategory.COMPLAINT: "クレーム・齟齬",
    RoleplayCategory.NEXT_COMMITMENT: "次回合意",
}

# カテゴリから検索クエリへの変換。
#
# **カテゴリ名をそのまま検索文にしない。** 「値引き」の2文字では
# lexical 側の word_similarity がほぼ立たず、semantic 側も手掛かりが足りない。
# 実際のナレッジ本文に現れる言い回しへ寄せた文にしておく。
CATEGORY_QUERIES: dict[RoleplayCategory, str] = {
    RoleplayCategory.NEEDS_DISCOVERY: "顧客の課題や困りごとを深掘りして聞き出した場面",
    RoleplayCategory.PRICE_OBJECTION: "価格が高い・値引きしてほしいと言われたときの対応",
    RoleplayCategory.OBJECTION: "顧客から反対意見や懸念を示されたときの切り返し",
    RoleplayCategory.COMPLAINT: "クレームや認識の齟齬が起きたときの対応",
    RoleplayCategory.NEXT_COMMITMENT: "次回の打ち合わせや次の行動を合意した場面",
}


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class TurnRole(StrEnum):
    """発言者。

    `system` を持たせないのは、指示文が発言として保存されると
    フィードバック生成の入力に混ざり、利用者が言っていないことを
    評価対象にしてしまうため。
    """

    LEARNER = "learner"
    CUSTOMER = "customer"


class InputMode(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    GENERATED = "generated"


class UsageType(StrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"


class RubricVerdict(StrEnum):
    MET = "met"
    PARTIAL = "partial"
    NOT_MET = "not_met"


# ---------------------------------------------------------------------------
# Qwen の出力契約
# ---------------------------------------------------------------------------


class RubricItem(BaseModel):
    """フィードバックの評価観点。

    「共感力82点」のような根拠のない総合点を出さないために、
    **この場面で何ができていれば良いか**を先に言語化して固定する。
    観点を後から動かすと、同じ練習の結果を比べられなくなる。
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=40, description="英小文字とアンダースコアの識別子")
    label: str = Field(min_length=1, max_length=60, description="画面に出す日本語の観点名")


class RoleplayScenario(BaseModel):
    """Qwen が生成する練習シナリオ。

    **Knowledge ID / Evidence ID / 発話番号を持たせない。**
    LLM に ID を書かせると実在しない値を返し、利用者が検証できなくなる。
    出典はサーバが `roleplay_session_knowledge` から組み立てる（CLAUDE.md 6章）。

    `extra="forbid"` にしているのは、想定外のキーを黙って捨てると
    「シナリオに無いはずの設定」が混ざったまま気づけないため。
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=60, description="練習場面の見出し")
    situation: str = Field(min_length=1, max_length=400, description="直前までの商談状況")
    learner_goal: str = Field(min_length=1, max_length=200, description="今回の練習で達成する目標")
    customer_persona: str = Field(min_length=1, max_length=200, description="顧客像と話し方の特徴")
    opening_line: str = Field(
        min_length=1, max_length=200, description="顧客役の最初の発言。この一言から練習が始まる"
    )
    max_turns: int = Field(
        default=2,
        ge=1,
        le=3,
        description="後輩が発言できる最大回数。1往復デモでは1にする",
    )
    rubric: list[RubricItem] = Field(
        min_length=1,
        max_length=4,
        description="フィードバックの評価観点。0件は許さない",
    )


class CustomerReply(BaseModel):
    """顧客役の1返答。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=300, description="顧客の発言。1〜3文")


class RubricResult(BaseModel):
    """観点ごとの判定。

    `label` を Qwen に書かせず、シナリオ側の `RubricItem` から
    サーバが埋め直す。観点名まで生成させると、評価対象がすり替わるため。
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=40)
    verdict: RubricVerdict
    comment: str = Field(min_length=1, max_length=300, description="そう判定した理由")
    label: str = Field(default="", description="サーバが rubric から埋める。Qwenの値は使わない")


class GeneratedFeedback(BaseModel):
    """Qwen が生成するフィードバック本体。出典はここに含めない。"""

    model_config = ConfigDict(extra="forbid")

    rubric_results: list[RubricResult] = Field(min_length=1, max_length=4)
    strengths: list[str] = Field(default_factory=list, max_length=4)
    improvements: list[str] = Field(default_factory=list, max_length=4)
    next_phrase: str = Field(
        min_length=1, max_length=200, description="次に試す一言。そのまま口に出せる形で書く"
    )
    focus_next_try: str = Field(min_length=1, max_length=200, description="再挑戦時に意識する1点")


# ---------------------------------------------------------------------------
# API の入出力
# ---------------------------------------------------------------------------


class RoleplaySessionCreate(BaseModel):
    """セッション作成の入力。

    3つの開始経路（質問 / AIチャットのCitation / 場面カテゴリ）を
    1つのエンドポイントで受ける。どれも「どのKnowledgeで練習するか」を
    決めるための手掛かりでしかないため、経路ごとにAPIを分ける必要がない。
    """

    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, min_length=1, max_length=500)
    knowledge_id: UUID | None = Field(
        default=None, description="AIチャットのCitationから開始する場合に指定する"
    )
    category: RoleplayCategory | None = None
    max_turns: int = Field(
        default=2,
        ge=1,
        le=3,
        description="ラウンドロビン用に1往復へ落とす場合は1を指定する",
    )

    @model_validator(mode="after")
    def _require_any_seed(self) -> RoleplaySessionCreate:
        """3つのうち最低1つは必要。

        全て空だと「何を練習したいのか」が決まらず、検索クエリも作れない。
        422 で弾いた方が、無関係なナレッジで練習させるより良い。
        """
        if self.query is None and self.knowledge_id is None and self.category is None:
            raise ValueError("query / knowledge_id / category のいずれかを指定してください")
        return self


class LearnerTurnRequest(BaseModel):
    """後輩の回答。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=2_000)
    input_mode: InputMode = Field(
        default=InputMode.TEXT,
        description="audio はSTT結果を人が確認・修正して送ったことを表す",
    )

    @model_validator(mode="after")
    def _reject_generated(self) -> LearnerTurnRequest:
        """`generated` はサーバが顧客発言に付ける値。

        クライアントから送れると、AIが作った発言を後輩の回答として
        保存でき、フィードバックの前提が崩れる。
        """
        if self.input_mode is InputMode.GENERATED:
            raise ValueError("input_mode に generated は指定できません")
        return self


class RoleplayTranscription(BaseModel):
    """後輩の音声回答を文字起こしした結果。

    **この時点ではまだ発言として保存していない。**
    文字起こしの誤りは後段のLLMでは直せない（欠落や幻覚は誤りに見えないまま
    自然な文で埋められる）ため、人が確認・修正してから `turns/text` へ送る
    2段構えにしている。音声のまま顧客返答まで進めると、誤認識に対して
    顧客役が答え、フィードバックまでその前提で進んでしまう。

    元の音声は返さない。サーバにも残さない（計画書15章）。
    """

    text: str = Field(description="文字起こし結果。画面で修正してから送信する")
    language: str | None = None
    duration_sec: float = Field(description="録音の長さ。短すぎる回答の検知に使う")


class RoleplayTurn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence_no: int
    role: TurnRole
    content: str
    input_mode: InputMode
    created_at: datetime


class ReferencedUtterance(BaseModel):
    """出典として示す1発話。

    `start_sec` を持たせているのは、将来音声原本を保存したときに
    その位置から再生するため。MVPでは時刻の表示までを必須とする（計画書6章）。
    """

    model_config = ConfigDict(from_attributes=True)

    sequence_no: int
    speaker: str
    start_sec: float
    end_sec: float
    content: str


class ReferencedKnowledge(BaseModel):
    """セッションが実際に使ったナレッジ。

    **Qwenの出力から作らない。** `roleplay_session_knowledge` に
    保存した行だけから組み立てる。

    `limitations` を必ず載せるのは、成功例の模倣だけを正解にしないため。
    「この条件では使えない」が見えないと、後輩が場面を選ばず真似る。
    """

    knowledge_id: UUID
    title: str
    usage_type: UsageType
    rank: int
    data_source_id: UUID | None = None
    file_name: str | None = None
    applicable_situations: str | None = None
    limitations: str | None = None
    utterances: list[ReferencedUtterance] = Field(default_factory=list)


class RoleplayFeedback(BaseModel):
    """最終フィードバック。出典はサーバが付ける。"""

    rubric_results: list[RubricResult]
    strengths: list[str]
    improvements: list[str]
    next_phrase: str
    focus_next_try: str
    created_at: datetime


class RoleplaySession(BaseModel):
    """画面が必要とするセッションの全体像。

    再読込とデバッグで同じものを見られるよう、`GET` と各操作の応答で
    同じ型を返す。画面側が状態を継ぎ足しで持たなくて済む。
    """

    session_id: UUID
    status: SessionStatus
    query: str
    scenario: RoleplayScenario
    turns: list[RoleplayTurn]
    references: list[ReferencedKnowledge]
    feedback: RoleplayFeedback | None = None
    learner_turns_used: int
    remaining_learner_turns: int
    created_at: datetime
    completed_at: datetime | None = None


class CategoryOption(BaseModel):
    """場面カテゴリの選択肢。画面のボタンをこの応答から作る。"""

    key: RoleplayCategory
    label: str
