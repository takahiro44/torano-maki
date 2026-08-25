"""商談要約の生成。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.knowledge import CallSummary, GenerateSummaryRequest
from app.services.extraction import LlmNotConfiguredError, LlmRequestError
from app.services.summary import process_segments_to_summary

router = APIRouter(prefix="/summaries", tags=["summaries"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("/generate", response_model=CallSummary, status_code=status.HTTP_201_CREATED)
def generate_call_summary(payload: GenerateSummaryRequest, db: DbSession) -> CallSummary:
    try:
        row = process_segments_to_summary(payload.data_source_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CallSummary.model_validate(row)
