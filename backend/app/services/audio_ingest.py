"""音声ファイルを data_sources / utterance_segments に取り込む。

api/ingest.py に直接書かず切り出しているのは、**後で非同期ジョブ化するため。**
音声が長くなって同期リクエストで待てなくなったら、
この関数をそのまま BackgroundTasks に渡せば済むようにしておく
（同期で書いた分が捨て実装にならないようにする）。
"""

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.knowledge import SourceType
from app.models.tables import DataSourceTable, UtteranceSegmentTable
from app.services.transcription import Transcript, transcribe

logger = logging.getLogger(__name__)

# faster-whisper は話者分離をしない。誰の発話かは音声だけからは決まらないため、
# 埋めずに「不明」であることを明示する。ここを "営業" などと決め打ちすると、
# 後で話者分離を入れたときに嘘のデータが残る。
_UNKNOWN_SPEAKER = "unknown"


def process_audio_to_source(
    audio_path: Path,
    file_name: str,
    db: Session,
) -> tuple[DataSourceTable, Transcript]:
    """音声 → 文字起こし → data_sources + utterance_segments。

    セグメントを保存するのは、ナレッジの根拠を「何分何秒の発話か」まで
    辿れるようにするため（CLAUDE.md 6章の出典要件）。
    テキスト投入では時刻が無くダミー値が入るが、音声経由なら本物が入る。
    """
    transcript = transcribe(audio_path)

    source = DataSourceTable(
        source_type=SourceType.AUDIO.value,
        file_name=file_name[:255],
    )
    db.add(source)
    db.flush()

    for i, segment in enumerate(transcript.segments, start=1):
        db.add(
            UtteranceSegmentTable(
                data_source_id=source.id,
                sequence_no=i,
                speaker=_UNKNOWN_SPEAKER,
                start_sec=segment.start,
                end_sec=segment.end,
                content=segment.text,
            )
        )

    db.commit()
    db.refresh(source)
    logger.info(
        "音声を取り込みました: %s (%d セグメント / %d 文字)",
        file_name,
        len(transcript.segments),
        len(transcript.text),
    )
    return source, transcript
