"""LLMによる CBR 構造化ナレッジの抽出。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import (
    CBR_FIELD_LABELS,
    ExtractedKnowledge,
    ExtractionResult,
    KnowledgeStatus,
)
from app.models.tables import (
    DataSourceTable,
    KnowledgeEvidenceTable,
    KnowledgeUnitTable,
    UtteranceSegmentTable,
)
from app.services.embedding import generate_embedding
from app.services.search_text import generate_search_text_from_mapping

logger = logging.getLogger(__name__)

_SCHEMA_NAME = "knowledge_extraction"

_SYSTEM_PROMPT = """あなたは営業ナレッジ抽出の専門家です。
入力テキストから営業担当者の経験・ノウハウを構造化して抽出してください。

## 抽出フォーマット

1件につき以下を抽出：

- title: 見出し（30文字以内）
- situation: 状況（何が起きたか）
- problem: 顧客課題（顧客側の障壁・懸念）
- judgment: 判断（営業が何を考え、どう判断したか）
- action: 行動（具体的に何をしたか）
- reasoning: 理由（なぜその判断・行動を選んだか）
- outcome: 結果（どうなったか）
- lesson: 学び（他の商談でも使える教訓）
- applicable_situations: 適用場面（どんな場面で使えるか）
- limitations: 制約・非適用場面（使えない場面や注意点）
- industry: 業界（該当する場合のみ）
- product: 商材（該当する場合のみ）
- sales_stage: 商談フェーズ（初回/提案/クロージング等、該当する場合のみ）

## ルール

- 1テキストから複数件抽出可
- 抽出できなければ空配列を返す
- 各項目は1〜3文で簡潔に
- テキストに書かれた事実に基づく（推測しない）
- 情報不足の項目は null（無理に埋めない）
- title は「〜の対応法」「〜への切り返し」のような検索しやすい表現
- 出力は指定の JSON Schema に厳密に従う。説明文やコードフェンスは付けない
"""

_CHUNK_CHARS = 4000
_CHUNK_OVERLAP = 250
_MAX_CHUNKS = 8
_EXTRACT_TIMEOUT = 90.0


class LlmNotConfiguredError(RuntimeError):
    """BASE_URL / MODEL_NAME が空のときに上げる。"""


class LlmRequestError(RuntimeError):
    """vLLM への到達に失敗したときに上げる。"""


def extraction_json_schema() -> dict[str, Any]:
    return ExtractionResult.model_json_schema()


def build_extraction_payload(text: str, *, model: str) -> dict[str, Any]:
    schema = extraction_json_schema()
    schema_text = json.dumps(schema, ensure_ascii=False)
    user_content = (
        "次の JSON Schema に従って、ナレッジ配列を JSON だけ出力してください。\n"
        f"{schema_text}\n\n"
        "以下のテキストからナレッジを抽出してください:\n\n"
        f"{text}"
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


def format_item_as_content(item: ExtractedKnowledge) -> str:
    blocks: list[str] = []
    for field_name, label in CBR_FIELD_LABELS:
        value = getattr(item, field_name, None)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        blocks.append(f"【{label}】\n{text}")
    return "\n\n".join(blocks)


def split_source_text(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    if len(normalized) <= _CHUNK_CHARS:
        return [normalized]

    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length and len(chunks) < _MAX_CHUNKS:
        end = min(start + _CHUNK_CHARS, length)
        if end < length:
            window = normalized[start:end]
            break_at = max(window.rfind("\n\n"), window.rfind("。"), window.rfind("\n"))
            if break_at >= _CHUNK_CHARS // 2:
                end = start + break_at + 1
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        nxt = end - _CHUNK_OVERLAP
        start = nxt if nxt > start else end
    return chunks


def source_was_truncated(text: str) -> bool:
    n = len(text.replace("\r\n", "\n").strip())
    capacity = _CHUNK_CHARS * _MAX_CHUNKS - _CHUNK_OVERLAP * max(_MAX_CHUNKS - 1, 0)
    return n > capacity


def extract_knowledge_with_sources(text: str) -> list[tuple[ExtractedKnowledge, str]]:
    settings = get_settings()
    if not settings.is_llm_configured:
        raise LlmNotConfiguredError("BASE_URL / MODEL_NAME が未設定。.env を確認すること")

    chunks = split_source_text(text)
    if not chunks:
        return []

    collected: list[tuple[ExtractedKnowledge, str]] = []
    seen_titles: set[str] = set()
    url = settings.base_url.rstrip("/") + "/chat/completions"
    for chunk in chunks:
        items = _extract_one_chunk(chunk, model=settings.model_name, url=url)
        for item in items:
            key = item.title.strip()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            collected.append((item, chunk))
    return collected


def extract_knowledge(text: str) -> ExtractionResult:
    """テキストから構造化ナレッジを抽出する。パース失敗時は空結果。"""
    try:
        items = [item for item, _ in extract_knowledge_with_sources(text)]
    except LlmNotConfiguredError:
        raise
    except LlmRequestError:
        raise
    return ExtractionResult(items=items)


def _extract_one_chunk(text: str, *, model: str, url: str) -> list[ExtractedKnowledge]:
    payload = build_extraction_payload(text, model=model)
    try:
        with httpx.Client(timeout=_EXTRACT_TIMEOUT) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        logger.exception("vLLM への抽出リクエストに失敗しました")
        raise LlmRequestError(f"vLLM に接続できません: {exc}") from exc

    try:
        message = body["choices"][0]["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError):
        logger.error("vLLM の応答形式が想定外です: %r", body)
        return []

    if not content or not str(content).strip():
        logger.error("vLLM が空の content を返しました")
        return []

    return _parse_extraction_json(str(content)).items


def _parse_extraction_json(raw: str) -> ExtractionResult:
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"(\{.*\}|\[.*\])", stripped, flags=re.DOTALL)
    if match:
        stripped = match.group(1)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        logger.exception("抽出結果が JSON ではありません: %s", raw[:300])
        return ExtractionResult(items=[])

    if isinstance(data, list):
        data = {"items": data}
    try:
        return ExtractionResult.model_validate(data)
    except Exception:
        logger.exception("抽出結果のスキーマが合いません")
        return ExtractionResult(items=[])


def knowledge_row_from_extracted(
    item: ExtractedKnowledge,
    *,
    data_source_id: UUID | None,
    status: str = KnowledgeStatus.DRAFT.value,
) -> KnowledgeUnitTable:
    dump = item.model_dump()
    search_text = generate_search_text_from_mapping(dump)
    settings = get_settings()
    return KnowledgeUnitTable(
        data_source_id=data_source_id,
        knowledge_type=dump["knowledge_type"],
        title=dump["title"],
        situation=dump["situation"],
        problem=dump["problem"],
        judgment=dump["judgment"],
        action=dump["action"],
        reasoning=dump["reasoning"],
        outcome=dump["outcome"],
        lesson=dump["lesson"],
        applicable_situations=dump["applicable_situations"],
        limitations=dump["limitations"],
        industry=dump["industry"],
        product=dump["product"],
        sales_stage=dump["sales_stage"],
        search_text=search_text,
        embedding=generate_embedding(search_text),
        embedding_model=settings.embedding_model,
        status=status,
    )


def process_text_to_knowledge(
    text: str,
    db: Session,
    data_source_id: UUID | None = None,
    *,
    status: str = KnowledgeStatus.DRAFT.value,
) -> tuple[list[KnowledgeUnitTable], list[str]]:
    """テキスト → 構造化 → search_text → embedding → DB。"""
    notes: list[str] = []
    if source_was_truncated(text):
        notes.append(
            "入力が長いため先頭から分割して抽出しました。後半が落ちている可能性があります。"
        )

    if data_source_id is None:
        source = DataSourceTable(source_type="manual")
        db.add(source)
        db.flush()
        data_source_id = source.id

    pairs = extract_knowledge_with_sources(text)
    saved: list[KnowledgeUnitTable] = []
    excerpt_to_segment: dict[str, UtteranceSegmentTable] = {}
    max_seq = db.execute(
        select(func.max(UtteranceSegmentTable.sequence_no)).where(
            UtteranceSegmentTable.data_source_id == data_source_id
        )
    ).scalar()
    next_seq = int(max_seq or 0) + 1

    for item, excerpt in pairs:
        if not item.title.strip():
            continue
        row = knowledge_row_from_extracted(
            item,
            data_source_id=data_source_id,
            status=status,
        )
        db.add(row)
        db.flush()

        segment = excerpt_to_segment.get(excerpt)
        if segment is None:
            segment = UtteranceSegmentTable(
                data_source_id=data_source_id,
                sequence_no=next_seq,
                speaker="source",
                start_sec=0.0,
                end_sec=0.01,
                content=excerpt,
            )
            next_seq += 1
            db.add(segment)
            db.flush()
            excerpt_to_segment[excerpt] = segment

        db.add(
            KnowledgeEvidenceTable(
                knowledge_id=row.id,
                start_utterance_id=segment.id,
                end_utterance_id=segment.id,
            )
        )
        saved.append(row)
    db.commit()
    for row in saved:
        db.refresh(row)
    return saved, notes
