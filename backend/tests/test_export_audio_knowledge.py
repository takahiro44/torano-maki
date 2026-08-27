"""登録済み音声を班員向けJSONへ書き出す処理のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.tables import (
    DataSourceTable,
    KnowledgeEvidenceTable,
    KnowledgeUnitTable,
    UtteranceSegmentTable,
)
from scripts.export_audio_knowledge import (
    build_export_result,
    find_audio_source,
    write_result,
)
from scripts.load_extraction_json import load_result


def _registered_audio(db: Session) -> DataSourceTable:
    source = DataSourceTable(source_type="audio", file_name="yesterday.wav")
    db.add(source)
    db.flush()
    segment = UtteranceSegmentTable(
        data_source_id=source.id,
        sequence_no=1,
        speaker="unknown",
        start_sec=0.0,
        end_sec=3.5,
        content="A社では30名で段階導入する提案をした。",
    )
    db.add(segment)
    db.flush()
    knowledge = KnowledgeUnitTable(
        data_source_id=source.id,
        knowledge_type="sales_knowhow",
        title="30名での段階導入",
        action="A社の営業部30名だけで段階導入する提案をした。",
        search_text="タイトル: 30名での段階導入",
        status="confirmed",
    )
    db.add(knowledge)
    db.flush()
    db.add(
        KnowledgeEvidenceTable(
            knowledge_id=knowledge.id,
            start_utterance_id=segment.id,
            end_utterance_id=segment.id,
        )
    )
    db.flush()
    return source


def test_find_audio_source_は最新またはIDで選べる(db: Session) -> None:
    source = _registered_audio(db)

    assert find_audio_source(db).id == source.id
    assert find_audio_source(db, source_id=source.id).id == source.id
    assert find_audio_source(db, file_name="yesterday.wav").id == source.id


def test_export_json_は既存の復元スクリプトで読める(db: Session, tmp_path: Path) -> None:
    source = _registered_audio(db)
    result = build_export_result(db, source)
    output = tmp_path / "audio.json"

    write_result(result, output)
    loaded = load_result(output)

    assert loaded.data_sources[0].id == source.id
    assert loaded.data_sources[0].occurred_at is None
    assert loaded.data_sources[0].origin == "real"
    assert loaded.data_sources[0].review_status == "unreviewed"
    assert loaded.utterance_segments[0].content.startswith("A社")
    assert loaded.knowledge_units[0].title == "30名での段階導入"
    assert loaded.knowledge_units[0].embedding is None
    assert loaded.knowledge_evidence[0].knowledge_id == loaded.knowledge_units[0].id


def test_draftしかない音声はconfirmedとして出力しない(db: Session) -> None:
    source = DataSourceTable(source_type="audio", file_name="draft.wav")
    db.add(source)
    db.flush()
    db.add(
        KnowledgeUnitTable(
            data_source_id=source.id,
            knowledge_type="sales_knowhow",
            title="未承認",
            search_text="タイトル: 未承認",
            status="draft",
        )
    )
    db.flush()

    with pytest.raises(ValueError, match="画面でナレッジ化・承認"):
        build_export_result(db, source)
