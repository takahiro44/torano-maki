from datetime import UTC, datetime
from pathlib import Path

import pytest

from run_experiment import (
    materialize_result,
    parse_json_object,
    validate_semantics,
)
from schema import ExperimentResult, LlmExtraction, TranscriptDocument


def _transcript() -> TranscriptDocument:
    return TranscriptDocument.model_validate(
        {
            "text": "困っています。詳しく教えてください。",
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "困っています。"},
                {"start": 2.0, "end": 4.0, "text": "詳しく教えてください。"},
            ],
            "language": "ja",
        }
    )


def _extraction() -> LlmExtraction:
    return LlmExtraction.model_validate(
        {
            "speaker_assignments": [
                {"sequence_no": 1, "speaker": "customer"},
                {"sequence_no": 2, "speaker": "salesperson"},
            ],
            "knowledge_units": [
                {
                    "knowledge_type": "sales_technique",
                    "title": "課題を具体化する",
                    "situation": "顧客が漠然と困っている",
                    "problem": None,
                    "judgment": None,
                    "action": "具体例を聞く",
                    "reasoning": None,
                    "outcome": None,
                    "lesson": "抽象的な課題は質問で具体化する",
                    "applicable_situations": "初回ヒアリング",
                    "limitations": None,
                    "industry": None,
                    "product": None,
                    "sales_stage": "discovery",
                    "evidence": [{"start_sequence_no": 1, "end_sequence_no": 2}],
                }
            ],
            "call_summary": {
                "summary": "顧客の課題を確認した。",
                "customer_needs": ["課題の解決"],
                "proposals": [],
                "decisions": [],
                "next_actions": ["詳細を確認する"],
            },
        }
    )


def test_parse_json_object_accepts_code_fence() -> None:
    assert parse_json_object('説明\n```json\n{"value": 1}\n```') == {"value": 1}


def test_validate_semantics_rejects_missing_speaker() -> None:
    extraction = _extraction().model_copy(
        update={"speaker_assignments": _extraction().speaker_assignments[:1]}
    )
    with pytest.raises(ValueError, match="missing"):
        validate_semantics(extraction, segment_count=2)


def test_materialize_result_connects_foreign_keys(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(_transcript().model_dump_json(), encoding="utf-8")
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"RIFF-test")
    fixed_now = datetime(2026, 8, 24, tzinfo=UTC)

    result = materialize_result(
        _transcript(),
        _extraction(),
        transcript_path,
        audio_path,
        now=fixed_now,
    )

    source_id = result.data_sources[0].id
    knowledge = result.knowledge_units[0]
    evidence = result.knowledge_evidence[0]
    assert all(item.data_source_id == source_id for item in result.utterance_segments)
    assert knowledge.data_source_id == source_id
    assert evidence.knowledge_id == knowledge.id
    assert evidence.start_utterance_id == result.utterance_segments[0].id
    assert evidence.end_utterance_id == result.utterance_segments[1].id
    assert knowledge.embedding is None
    assert "課題を具体化する" in knowledge.search_text


def test_committed_output_matches_result_contract() -> None:
    output_path = Path(__file__).resolve().parents[1] / "output" / "knowledge_extraction.json"
    result = ExperimentResult.model_validate_json(output_path.read_text(encoding="utf-8"))

    assert len(result.data_sources) == 1
    assert len(result.utterance_segments) == 74
    assert len(result.knowledge_units) == 5
    assert len(result.knowledge_evidence) == 8
    assert len(result.call_summaries) == 1
