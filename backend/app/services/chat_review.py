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
from app.models.chat_review import ChatReviewSummary
from app.models.knowledge import KnowledgeStatus
from app.models.tables import ChatReviewTable, KnowledgeUnitTable
from app.services.extraction import (
    LlmNotConfiguredError,
    LlmRequestError,
    process_text_to_knowledge,
)

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


def create_chat_review(messages: list[ChatMessage], db: Session) -> ChatReviewTable:
    """会話ログ → 要約 → chat_reviews（status=pending）として保存。"""
    result = generate_chat_review_summary(messages)
    row = ChatReviewTable(
        chat_history=[m.model_dump(mode="json") for m in messages],
        summary=result.summary,
        understood_points=result.understood_points,
        knowledge_gaps=result.knowledge_gaps,
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def respond_to_chat_review(review_id: UUID, response_text: str, db: Session) -> ChatReviewTable:
    """上司の回答 → confirmedなナレッジとして登録 → chat_reviewsをansweredに更新。"""
    row = db.execute(
        select(ChatReviewTable).where(ChatReviewTable.id == review_id)
    ).scalar_one_or_none()
    if row is None:
        raise LookupError("ChatReview が見つかりません")
    if row.status == "answered":
        raise FileExistsError("このレビューは既に回答済みです")

    saved, _notes = process_text_to_knowledge(
        response_text, db, status=KnowledgeStatus.CONFIRMED.value
    )
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
