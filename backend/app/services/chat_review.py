"""AIチャットの会話ログ→上司レビュー→ナレッジ化。"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.chat import ChatMessage
from app.models.chat_review import (
    ChatReviewSummary,
    GapDbState,
    GapDiagnosis,
    GapKnowledgeHit,
    HearingQuestion,
    ReviewQuestion,
    SendChatReviewRequest,
    UnderstoodPoint,
)
from app.models.knowledge import KnowledgeStatus
from app.models.tables import ChatReviewTable, KnowledgeUnitTable
from app.services.extraction import (
    LlmNotConfiguredError,
    LlmRequestError,
    process_text_to_knowledge,
)
from app.services.search import search_knowledge

logger = logging.getLogger(__name__)

_SCHEMA_NAME = "chat_review_summary"
_SUMMARY_TIMEOUT = 90.0

_SYSTEM_PROMPT = """あなたは営業指導の専門家です。
後輩とAIチャットの会話ログから、後輩が理解できていた点と、
蓄積ナレッジだけでは埋まらなかった疑問点を抽出してください。

## 出力形式

JSON のみ出力。

{
  "summary": "会話全体の要約（3〜5文）",
  "understood_points": ["後輩が理解できていた事項1", "事項2"],
  "knowledge_gaps": ["ナレッジDBに不足していた疑問点1", "疑問点2"]
}

## ルール

- 該当なしの項目は空配列 []
- 各配列の要素は1文で簡潔に
- 出力は指定の JSON Schema に厳密に従う。説明文やコードフェンスは付けない
"""


def _messages_to_transcript(messages: list[ChatMessage]) -> str:
    return "\n".join(f"[{m.role.value}] {m.content}" for m in messages)


def build_review_summary_payload(transcript: str, *, model: str) -> dict:
    schema = ChatReviewSummary.model_json_schema()
    schema_text = json.dumps(schema, ensure_ascii=False)
    user_content = (
        "次の JSON Schema に従って要約を JSON だけ出力してください。\n"
        f"{schema_text}\n\n"
        "以下の会話ログを要約してください:\n\n"
        f"{transcript}"
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": _SCHEMA_NAME, "schema": schema},
        },
        "structured_outputs": {"json": schema},
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _parse_review_summary_json(raw: str) -> ChatReviewSummary:
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match:
        stripped = match.group(0)
    data = json.loads(stripped)
    return ChatReviewSummary.model_validate(data)


def generate_chat_review_summary(messages: list[ChatMessage]) -> ChatReviewSummary:
    settings = get_settings()
    if not settings.is_llm_configured:
        raise LlmNotConfiguredError("BASE_URL / MODEL_NAME が未設定。.env を確認すること")

    transcript = _messages_to_transcript(messages)
    url = settings.base_url.rstrip("/") + "/chat/completions"
    payload = build_review_summary_payload(transcript, model=settings.model_name)
    try:
        with httpx.Client(timeout=_SUMMARY_TIMEOUT) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        logger.exception("vLLM への要約リクエストに失敗しました")
        raise LlmRequestError(f"vLLM に接続できません: {exc}") from exc

    try:
        content = body["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmRequestError("vLLM の応答形式が想定外です") from exc
    if not content or not str(content).strip():
        raise LlmRequestError("vLLM が空の content を返しました")
    try:
        return _parse_review_summary_json(str(content))
    except Exception as exc:
        raise LlmRequestError("要約 JSON のパースに失敗しました") from exc


# --- 疑問点をナレッジDBに当てる ---
#
# **要約の実況（chat_review_stream）と送信の両方から呼ぶ。** 同じ判定を2箇所に
# 書くと、画面に「近いナレッジは無い」と出ていたものが、保存すると
# 「有る」に化けることが起きる。判定はここだけに置く。

_SEARCH_TOP_K = 3

# **コサイン類似度で判定する。** RRF の score は順位のための内部値で、
# 絶対値として閾値に使えない（models/knowledge.py:369）。
#
# 0.80 は暫定値。seed の15件と実レビュー数件で当ててから固定する（計画 9章）。
# 決めた値と根拠は docs/decisions.md に残すこと。
SEMANTIC_MATCH_THRESHOLD = 0.80

# 照合する問いの上限。1件ごとに埋め込み生成＋検索が走るため、
# 増やすと「上司に質問する」の待ち時間がそのまま伸びる（計画 3.7）
MAX_QUESTIONS_TO_MATCH = 6


def match_gap(db: Session, gap: str) -> GapDiagnosis:
    """疑問点1つをナレッジDBに当てる。

    **検索が落ちても打ち切らない。** 1件の照合に失敗しただけで要約ごと
    失うのは割に合わない。その疑問は `missing` として扱い、先へ進む。
    """
    try:
        hits = search_knowledge(db, gap, top_k=_SEARCH_TOP_K)
    except Exception:
        logger.exception("疑問点の照合に失敗しました: %s", gap)
        hits = []

    matched = [h for h in hits if h.semantic_score is not None]
    top = matched[0].semantic_score if matched else None
    if top is None or top < SEMANTIC_MATCH_THRESHOLD:
        return GapDiagnosis(gap=gap, db_state=GapDbState.MISSING)

    return GapDiagnosis(
        gap=gap,
        db_state=GapDbState.FOUND_BUT_UNREACHABLE,
        existing_knowledge=[
            GapKnowledgeHit(knowledge_id=h.id, title=h.title, semantic_score=h.semantic_score)
            for h in matched
            if h.semantic_score is not None and h.semantic_score >= SEMANTIC_MATCH_THRESHOLD
        ],
    )


def create_chat_review(payload: SendChatReviewRequest, db: Session) -> ChatReviewTable:
    """会話ログ（＋ヒアリング） → chat_reviews（status=pending）として保存。

    **ヒアリングが付いていたら要約を作り直さない。** 後輩は画面に出た要約と
    疑問点を読んだうえで「これも聞きたい」「ここは自信が無い」と答えている。
    ここで作り直すと、本人が答えたのとは別の文面が上司に届き、
    自己申告がどれに対する答えなのか分からなくなる。加えて、送信のたびに
    数十秒の生成をもう一度待たせることになる。

    **`db_state` だけは必ずサーバが埋め直す。** ナレッジDBに有るか無いかは
    クライアントに言わせない（models/chat_review.py の GapDbState）。
    """
    hearing = payload.hearing
    if hearing is None:
        # ヒアリング無しの経路。要約から機械的に組み立てる
        result = generate_chat_review_summary(payload.messages)
        summary = result.summary
        understood = [UnderstoodPoint(point=p) for p in result.understood_points]
        questions = [HearingQuestion(question=g) for g in result.knowledge_gaps]
        asked_by = None
        # ここでは照合しない。呼び出し元が待てる時間の前提が変わってしまう
        matched: list[ReviewQuestion] = [
            ReviewQuestion(question=q.question, source=q.source) for q in questions
        ]
    else:
        summary = hearing.summary
        understood = hearing.understood
        asked_by = (hearing.learner_name or "").strip() or None
        matched = [
            _resolve_question(db, q, asked_by=asked_by)
            for q in hearing.questions[:MAX_QUESTIONS_TO_MATCH]
        ]

    # 上司の時間を使う価値があるのは「DBに無い」方。並び順はサーバが決める（計画 3.4）
    matched.sort(key=lambda q: 0 if q.db_state is not GapDbState.FOUND_BUT_UNREACHABLE else 1)

    row = ChatReviewTable(
        chat_history=[m.model_dump(mode="json") for m in payload.messages],
        summary=summary,
        understood_points=[p.model_dump(mode="json") for p in understood],
        knowledge_gaps=[q.model_dump(mode="json") for q in matched],
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _resolve_question(
    db: Session, question: HearingQuestion, *, asked_by: str | None
) -> ReviewQuestion:
    """問い1つを保存する形にする。DBの状態はここで実際に検索して埋める。"""
    diagnosis = match_gap(db, question.question)
    return ReviewQuestion(
        question=question.question,
        source=question.source,
        db_state=diagnosis.db_state,
        existing_knowledge=diagnosis.existing_knowledge,
        asked_by=asked_by,
    )


def respond_to_chat_review(review_id: UUID, response_text: str, db: Session) -> ChatReviewTable:
    """上司の回答 → 下書きのナレッジとして抽出 → chat_reviewsをansweredに更新。

    **承認まではしない。** 承認は上司が中身を見てから押す（ingest と同じ）。
    レビュー自体は回答した時点で answered にする。下書きを承認し忘れたことと、
    上司が答えていないことは別の話であり、混ぜると未回答が消えなくなる。
    """
    row = db.execute(
        select(ChatReviewTable).where(ChatReviewTable.id == review_id)
    ).scalar_one_or_none()
    if row is None:
        raise LookupError("ChatReview が見つかりません")
    if row.status == "answered":
        raise FileExistsError("このレビューは既に回答済みです")

    # **下書きで作る。** 以前はここで confirmed にしていたため、上司の回答は
    # 構造化された中身を誰も見ないまま検索対象になっていた。抽出が一言だけ
    # 外していても直す機会が無い。ナレッジ登録（ingest）と同じく、
    # 出したものを確認・修正してから承認する（KnowledgeDrafts.tsx）
    saved, _notes = process_text_to_knowledge(response_text, db, status=KnowledgeStatus.DRAFT.value)
    if saved:
        row.answered_data_source_id = saved[0].data_source_id
    row.supervisor_response = response_text
    row.status = "answered"
    row.answered_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row


def get_created_knowledge(data_source_id: UUID | None, db: Session) -> list[KnowledgeUnitTable]:
    if data_source_id is None:
        return []
    return list(
        db.execute(
            select(KnowledgeUnitTable).where(KnowledgeUnitTable.data_source_id == data_source_id)
        )
        .scalars()
        .all()
    )
