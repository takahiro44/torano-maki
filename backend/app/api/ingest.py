"""ナレッジ蓄積のエンドポイント。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.knowledge import (
    ExtractedKnowledge,
    IngestPreviewItem,
    IngestTextRequest,
    IngestTextResponse,
    Knowledge,
    KnowledgeStatus,
)
from app.services.extraction import (
    LlmNotConfiguredError,
    LlmRequestError,
    extract_knowledge_with_sources,
    format_item_as_content,
    process_text_to_knowledge,
    source_was_truncated,
)

router = APIRouter(prefix="/ingest", tags=["ingest"])

DbSession = Annotated[Session, Depends(get_db)]


def _extract_pairs(raw_text: str) -> tuple[list[tuple[ExtractedKnowledge, str]], list[str]]:
    try:
        pairs = extract_knowledge_with_sources(raw_text)
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    notes: list[str] = []
    if source_was_truncated(raw_text):
        notes.append(
            "入力が長いため先頭から分割して抽出しました。後半が落ちている可能性があります。"
        )
    return pairs, notes


def _to_preview(pairs: list[tuple[ExtractedKnowledge, str]]) -> list[IngestPreviewItem]:
    items: list[IngestPreviewItem] = []
    for item, _excerpt in pairs:
        content = format_item_as_content(item)
        if not content:
            continue
        items.append(IngestPreviewItem(**item.model_dump(), content=content))
    return items


@router.post("/text/preview", response_model=IngestTextResponse)
def preview_text_extraction(payload: IngestTextRequest) -> IngestTextResponse:
    pairs, notes = _extract_pairs(payload.raw_text)
    return IngestTextResponse(
        raw_text=payload.raw_text, extracted=_to_preview(pairs), saved=[], notes=notes
    )


@router.post("/text", response_model=IngestTextResponse, status_code=status.HTTP_201_CREATED)
def ingest_text(payload: IngestTextRequest, db: DbSession) -> IngestTextResponse:
    """抽出して draft で保存する。検索対象にするには confirmed にする。"""
    try:
        saved, notes = process_text_to_knowledge(
            payload.raw_text,
            db,
            data_source_id=payload.data_source_id,
            status=KnowledgeStatus.DRAFT.value,
        )
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    extracted: list[IngestPreviewItem] = []
    for row in saved:
        item = ExtractedKnowledge.model_validate(
            {
                "title": row.title,
                "situation": row.situation,
                "problem": row.problem,
                "judgment": row.judgment,
                "action": row.action,
                "reasoning": row.reasoning,
                "outcome": row.outcome,
                "lesson": row.lesson,
                "applicable_situations": row.applicable_situations,
                "limitations": row.limitations,
                "industry": row.industry,
                "product": row.product,
                "sales_stage": row.sales_stage,
                "knowledge_type": row.knowledge_type,
            }
        )
        extracted.append(
            IngestPreviewItem(**item.model_dump(), content=format_item_as_content(item))
        )

    return IngestTextResponse(
        raw_text=payload.raw_text,
        extracted=extracted,
        saved=[Knowledge.model_validate(row) for row in saved],
        notes=notes,
    )
