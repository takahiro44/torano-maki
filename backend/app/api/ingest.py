"""ナレッジ蓄積のエンドポイント。"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.knowledge import (
    AudioTranscribeResponse,
    ExtractedKnowledge,
    IngestPreviewItem,
    IngestTextRequest,
    IngestTextResponse,
    Knowledge,
    KnowledgeStatus,
    TranscriptSegmentOut,
)
from app.services.audio_ingest import process_audio_to_source
from app.services.extraction import (
    LlmNotConfiguredError,
    LlmRequestError,
    extract_knowledge_with_sources,
    format_item_as_content,
    process_text_to_knowledge,
    source_was_truncated,
)
from app.services.transcription import SttNotConfiguredError, TranscriptionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

DbSession = Annotated[Session, Depends(get_db)]

# uvicorn にはボディサイズの上限が無く、指定しないと巨大なファイルを
# そのまま受け取ってディスクを埋める。商談1本を60分としても
# wav で 600MB 程度に収まるため、その2倍を上限にしている。
_MAX_AUDIO_BYTES = 200 * 1024 * 1024

# 音声のデコードは faster-whisper（PyAV）が行うため、ここは受け口の絞り込み。
# 想定外の拡張子を先に弾いておかないと、31秒待たされた末に
# サーバ側のデコードエラーになり、原因が分かりにくい。
_ALLOWED_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".webm", ".aac"})

# 一度に読む量。音声全体をメモリに載せないために分割して書き出す
_COPY_CHUNK_BYTES = 1024 * 1024


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


def _save_upload_to_temp(file: UploadFile, suffix: str) -> Path:
    """アップロードを一時ファイルへ書き出す。

    音声認識サーバへは multipart で送り直す必要があり、
    ファイルパスで扱えた方が transcribe() の差し替え（ローカル実行への出戻り）に
    そのまま対応できるため、いったんディスクに落とす。
    """
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    total = 0
    try:
        while chunk := file.file.read(_COPY_CHUNK_BYTES):
            total += len(chunk)
            if total > _MAX_AUDIO_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        f"音声ファイルが大きすぎます（上限 {_MAX_AUDIO_BYTES // (1024 * 1024)}MB）"
                    ),
                )
            tmp.write(chunk)
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise
    tmp.close()

    if total == 0:
        os.unlink(tmp.name)
        raise HTTPException(status_code=400, detail="音声ファイルが空です")
    return Path(tmp.name)


@router.post(
    "/audio/transcribe",
    response_model=AudioTranscribeResponse,
    status_code=status.HTTP_201_CREATED,
)
def transcribe_audio(
    db: DbSession,
    file: Annotated[UploadFile, File(description="商談音声（wav / mp3 / m4a など）")],
) -> AudioTranscribeResponse:
    """音声を文字起こしし、data_sources と utterance_segments に保存する。

    **同期で処理する。** DGX上のGPUで実時間の約17倍速で走るため
    （8分50秒の音声で31秒）、ジョブ管理を足すコストに見合わないと判断した。
    音声が20分を超えるのが常態になったら BackgroundTasks へ移すこと
    （services/audio_ingest.py がそのままジョブ本体になる）。

    ナレッジ化はここでは行わない。文字起こしを人が確認してから
    /ingest/text に渡す（理由は AudioTranscribeResponse の docstring）。

    **`async def` にしないこと。** 中で同期のHTTPクライアントを使うため、
    async にするとイベントループを数十秒ブロックしてサーバ全体が止まる。
    """
    file_name = file.filename or "audio"
    suffix = Path(file_name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"対応していない形式です（{suffix or '拡張子なし'}）。"
            f"対応: {', '.join(sorted(_ALLOWED_SUFFIXES))}",
        )

    audio_path = _save_upload_to_temp(file, suffix)
    try:
        source, transcript = process_audio_to_source(audio_path, file_name, db)
    except SttNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TranscriptionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        # 音声はリポジトリにもサーバにも残さない（CLAUDE.md 4.2）
        audio_path.unlink(missing_ok=True)

    segments = [
        TranscriptSegmentOut(
            sequence_no=i,
            start_sec=seg.start,
            end_sec=seg.end,
            text=seg.text,
        )
        for i, seg in enumerate(transcript.segments, start=1)
    ]
    return AudioTranscribeResponse(
        data_source_id=source.id,
        file_name=file_name,
        text=transcript.text,
        language=transcript.language,
        duration_sec=segments[-1].end_sec if segments else 0.0,
        segments=segments,
    )
