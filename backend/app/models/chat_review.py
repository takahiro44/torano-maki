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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.chat import ChatMessage, ChatStreamErrorCode
from app.models.knowledge import Knowledge


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


class UnderstandingLevel(StrEnum):
    """その論点を後輩がどこまで分かっているか。

    **`SHAKY` を独立させる理由。** 2値にすると「AIの答えを繰り返しただけ」が
    理解できていた側へ入る。そこは上司が最も教えるべき層であり、
    畳んでしまうと教えどころが1つ消える。

    **決めるのはAIではなく本人。** 既定は要約が出した `UNDERSTOOD` だが、
    送る前のヒアリングで本人が下げられる。自分が怪しいと思っているかどうかは、
    会話ログからは読み取れない。
    """

    UNDERSTOOD = "understood"
    SHAKY = "shaky"
    UNKNOWN = "unknown"


class UnderstoodPoint(BaseModel):
    """理解できていた事項1件と、本人の自己申告。

    **文字列だった頃の行をそのまま読めるようにする。** `understood_points` は
    JSONB で、保存済みの行には `list[str]` が入っている。素の文字列を
    「本人が何も申告していない `understood`」として扱えば、DDLもDBの
    作り直しも要らずに移行できる。
    """

    model_config = ConfigDict(extra="forbid")

    point: str = Field(min_length=1, max_length=400)
    level: UnderstandingLevel = UnderstandingLevel.UNDERSTOOD

    @classmethod
    def upgrade(cls, value: object) -> object:
        """旧形式（素の文字列）を今の形へ寄せる。検証の前に通す。"""
        return {"point": value} if isinstance(value, str) else value


class QuestionSource(StrEnum):
    """その問いを誰が立てたか。

    上司にとって意味が違う。`AGENT` は会話ログからAIが拾ったもので、
    `LEARNER` はヒアリングで本人が「ここが分からなかった」と自分の言葉で
    書いたもの。後者は会話に現れていないため、**AIには決して出せない。**
    """

    AGENT = "agent"
    LEARNER = "learner"


class ReviewQuestion(BaseModel):
    """上司に投げる問い1つ。**保存されるのはこの形。**

    `db_state` / `existing_knowledge` は**サーバが実際に検索して埋める**
    （GapDbState と同じ理由）。`None` は「まだ照合していない」で、
    照合を通らない経路（ヒアリング無しの送信・旧形式の行）でだけ現れる。
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=400)
    source: QuestionSource = QuestionSource.AGENT
    db_state: GapDbState | None = None
    existing_knowledge: list[GapKnowledgeHit] = Field(default_factory=list)
    # **名前のために列を足さない。** 認証を作らない方針（CLAUDE.md 3.1）なので
    # 後輩を個人として識別する場所が無い。ヒアリングで名乗った名前を問いへ
    # 持たせておけば、DDLを触らずに「誰からの質問か」を上司へ出せる。
    asked_by: str | None = Field(default=None, max_length=40)

    @classmethod
    def upgrade(cls, value: object) -> object:
        """旧形式（素の文字列）を今の形へ寄せる。検証の前に通す。"""
        return {"question": value} if isinstance(value, str) else value


class HearingQuestion(BaseModel):
    """ヒアリングで確定した問い1つ。`db_state` はここに載せない。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=400)
    source: QuestionSource = QuestionSource.AGENT


class ReviewHearing(BaseModel):
    """送る前にAIが後輩から聞き取った結果。

    **ここだけはクライアントの言い分を採る。** 「自分はどこが怪しいか」
    「ほかに何を聞きたいか」は本人の中にしか無く、サーバが会話ログから
    作り直しても本人の答えにはならない。要約をサーバで再生成していたのは
    Agentの自己申告を信用しないためであって、人の申告はその対象ではない。

    ただし `db_state` は渡させない。**ナレッジDBに有るか無いかは、
    実際に検索したサーバだけが言える。**
    """

    model_config = ConfigDict(extra="forbid")

    learner_name: str | None = Field(default=None, max_length=40)
    summary: str = Field(min_length=1, max_length=4_000)
    understood: list[UnderstoodPoint] = Field(default_factory=list, max_length=12)
    questions: list[HearingQuestion] = Field(default_factory=list, max_length=8)


class SendChatReviewRequest(BaseModel):
    """「上司に質問する」ボタンの中身。

    `hearing` を付けずに送ると、これまでどおりサーバが要約から組み立てる
    （既存の呼び出し元を壊さないため）。
    """

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    hearing: ReviewHearing | None = None


class ChatReviewListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    summary: str
    status: str
    created_at: datetime
    answered_at: datetime | None = None


class ChatReviewDetail(BaseModel):
    """上司が開く1件。

    **旧形式の行をそのまま読める。** `understood_points` / `knowledge_gaps` は
    JSONB なので構造化した値をそのまま入れられるが、保存済みの行には
    `list[str]` が残っている。検証の前に寄せておけば、DDLもDBの作り直しも
    要らずに新しい画面へ移れる（計画 6章は作り直しを前提にしていたが、
    デモ用に貯めたレビューを捨てずに済む方を採った）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chat_history: list[ChatMessage]
    summary: str
    understood_points: list[UnderstoodPoint]
    knowledge_gaps: list[ReviewQuestion]
    status: str
    supervisor_response: str | None = None
    answered_data_source_id: UUID | None = None
    created_at: datetime
    answered_at: datetime | None = None
    # **見出しだけでなく中身ごと返す。** 回答から作られるのは下書きで、
    # 上司はこの画面で確認・修正してから承認する（ingest と同じ扱い）。
    # id と title だけでは、そのために画面から引き直すことになる
    created_knowledge: list[Knowledge] = Field(default_factory=list)
    # 誰からの質問か。`knowledge_gaps` に持たせた名前を API 層が写す。
    # 画面が配列の先頭を覗きに行かなくて済むようにするためだけの項目
    learner_name: str | None = None

    @field_validator("understood_points", mode="before")
    @classmethod
    def _upgrade_points(cls, value: object) -> object:
        if isinstance(value, list):
            return [UnderstoodPoint.upgrade(v) for v in value]
        return value

    @field_validator("knowledge_gaps", mode="before")
    @classmethod
    def _upgrade_gaps(cls, value: object) -> object:
        if isinstance(value, list):
            return [ReviewQuestion.upgrade(v) for v in value]
        return value


class RespondChatReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_text: str = Field(min_length=1, max_length=20_000)


# --- 実況つきの「まとめる」（docs/plan-interactive-coaching.md 機能A） ---
#
# **`ChatReviewSummary` は LLM への契約のまま据え置く。** モデルに出させるのは
# 会話ログから読み取れることだけで、ナレッジDBの状態（GapDbState）も
# 本人の自己申告（UnderstandingLevel）もここには入れない。


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
