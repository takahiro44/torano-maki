"""発言セグメントから商談要約を生成する。"""

from __future__ import annotations

import json
import logging
import re
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import CallSummaryDraft
from app.models.tables import CallSummaryTable, DataSourceTable, UtteranceSegmentTable
from app.services.extraction import LlmNotConfiguredError, LlmRequestError

logger = logging.getLogger(__name__)

_SCHEMA_NAME = "call_summary"
_SUMMARY_TIMEOUT = 90.0

_SYSTEM_PROMPT = """あなたは商談議事録の要約専門家です。
入力された発言記録から商談の要約を作成してください。

## 出力形式

JSON のみ出力。

{
  "summary": "商談全体の要約（3〜5文）",
  "customer_needs": ["顧客ニーズ1", "顧客ニーズ2"],
  "proposals": ["提案内容1", "提案内容2"],
  "decisions": ["決定事項1"],
  "next_actions": ["次回アクション1", "次回アクション2"]
}

## ルール

- 該当なしの項目は空配列 []
- summary は商談の流れが分かる簡潔な要約
- 各配列の要素は1文で簡潔に
- 出力は指定の JSON Schema に厳密に従う。説明文やコードフェンスは付けない
"""


def build_summary_payload(transcript: str, *, model: str) -> dict:
    schema = CallSummaryDraft.model_json_schema()
    schema_text = json.dumps(schema, ensure_ascii=False)
    user_content = (
        "次の JSON Schema に従って要約を JSON だけ出力してください。\n"
        f"{schema_text}\n\n"
        "以下の発言記録を要約してください:\n\n"
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


def _parse_summary_json(raw: str) -> CallSummaryDraft:
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match:
        stripped = match.group(1)
    data = json.loads(stripped)
    return CallSummaryDraft.model_validate(data)


def generate_summary(transcript: str) -> CallSummaryDraft:
    settings = get_settings()
    if not settings.is_llm_configured:
        raise LlmNotConfiguredError("BASE_URL / MODEL_NAME が未設定。.env を確認すること")

    url = settings.base_url.rstrip("/") + "/chat/completions"
    payload = build_summary_payload(transcript, model=settings.model_name)
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
        return _parse_summary_json(str(content))
    except Exception as exc:
        raise LlmRequestError("要約 JSON のパースに失敗しました") from exc


def process_segments_to_summary(data_source_id: UUID, db: Session) -> CallSummaryTable:
    """発言セグメント群 → 商談要約 → DB。"""
    source = db.execute(
        select(DataSourceTable).where(DataSourceTable.id == data_source_id)
    ).scalar_one_or_none()
    if source is None:
        raise LookupError("DataSource が見つかりません")

    existing = db.execute(
        select(CallSummaryTable).where(CallSummaryTable.data_source_id == data_source_id)
    ).scalar_one_or_none()
    if existing is not None:
        raise FileExistsError("この出典の要約は既にあります")

    segments = list(
        db.execute(
            select(UtteranceSegmentTable)
            .where(UtteranceSegmentTable.data_source_id == data_source_id)
            .order_by(UtteranceSegmentTable.sequence_no.asc())
        )
        .scalars()
        .all()
    )
    if not segments:
        raise ValueError("発言セグメントがありません")

    transcript = "\n".join(f"[{seg.speaker}] {seg.content}" for seg in segments)
    result = generate_summary(transcript)
    row = CallSummaryTable(
        data_source_id=data_source_id,
        summary=result.summary,
        customer_needs=result.customer_needs,
        proposals=result.proposals,
        decisions=result.decisions,
        next_actions=result.next_actions,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
