"""音声取り込みのテスト。音声認識サーバは呼ばない。

ネットワークに出るところ（transcribe）はモックする。
DGXが落ちているとCIも自分の手元も止まる状態にしないため。
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge import ExtractedKnowledge
from app.models.tables import DataSourceTable, UtteranceSegmentTable
from app.services.transcription import (
    Transcript,
    TranscriptionError,
    TranscriptSegment,
    _parse_response,
    _transcriptions_url,
)

_FAKE_VECTOR = [0.0] * 1024

_SEGMENTS = [
    TranscriptSegment(start=0.0, end=5.0, text="今の販売管理システムが古いという話でした。"),
    TranscriptSegment(start=5.0, end=9.5, text="受注のたびにExcelにも入力しています。"),
    TranscriptSegment(start=9.5, end=14.0, text="まず受注まわりだけ先に改善する案を出しました。"),
]
_TRANSCRIPT = Transcript(
    text="".join(s.text for s in _SEGMENTS),
    segments=_SEGMENTS,
    language="ja",
)


# --- URLの組み立て ---
#
# .env に /v1 まで書く人と /v1/audio/transcriptions まで書く人が出る。
# 間違えると405になり原因が分かりにくいので、両方吸収できることを固定する。
@pytest.mark.parametrize(
    "given",
    [
        "http://dgx:8082",
        "http://dgx:8082/",
        "http://dgx:8082/v1",
        "http://dgx:8082/v1/",
        "http://dgx:8082/v1/audio/transcriptions",
    ],
)
def test_STT_のURLはどの書き方でも同じ宛先になる(given: str) -> None:
    assert _transcriptions_url(given) == "http://dgx:8082/v1/audio/transcriptions"


# --- 応答のパース ---


def test_応答をTranscriptに変換する() -> None:
    result = _parse_response(
        {
            "text": "あいうえお",
            "segments": [
                {"start": 0.0, "end": 1.5, "text": " あいう "},
                {"start": 1.5, "end": 3.0, "text": "えお"},
            ],
            "language": "ja",
        }
    )
    assert result.text == "あいうえお"
    assert [s.text for s in result.segments] == ["あいう", "えお"]
    assert result.language == "ja"


def test_textが空でもセグメントから復元する() -> None:
    result = _parse_response(
        {"text": "", "segments": [{"start": 0.0, "end": 1.0, "text": "こんにちは"}]}
    )
    assert result.text == "こんにちは"


def test_無音で結果が空なら分かるエラーにする() -> None:
    # 空文字のまま先へ進むと、抽出が0件になって「LLMが悪い」ように見えてしまう
    with pytest.raises(TranscriptionError):
        _parse_response({"text": "", "segments": []})


# --- エンドポイント ---


def test_音声を投入するとaudioのデータソースと発話が残る(client: TestClient, db: Session) -> None:
    with patch("app.services.audio_ingest.transcribe", return_value=_TRANSCRIPT):
        res = client.post(
            "/ingest/audio/transcribe",
            files={"file": ("shodan.wav", b"dummy-audio-bytes", "audio/wav")},
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["file_name"] == "shodan.wav"
    assert body["duration_sec"] == 14.0
    assert len(body["segments"]) == 3
    assert body["segments"][0]["sequence_no"] == 1

    source = db.get(DataSourceTable, body["data_source_id"])
    assert source is not None
    assert source.source_type == "audio"

    rows = (
        db.execute(
            select(UtteranceSegmentTable)
            .where(UtteranceSegmentTable.data_source_id == source.id)
            .order_by(UtteranceSegmentTable.sequence_no)
        )
        .scalars()
        .all()
    )
    # 時刻が本物であること。ここがダミーだと根拠を音声の位置まで辿れない
    assert [(r.start_sec, r.end_sec) for r in rows] == [(0.0, 5.0), (5.0, 9.5), (9.5, 14.0)]


def test_対応していない拡張子は文字起こし前に弾く(client: TestClient) -> None:
    # 31秒待たせた末にサーバ側のデコードエラーになるのを防ぐ
    res = client.post(
        "/ingest/audio/transcribe",
        files={"file": ("memo.txt", b"not audio", "text/plain")},
    )
    assert res.status_code == 400
    assert ".txt" in res.json()["detail"]


def test_空のファイルは弾く(client: TestClient) -> None:
    res = client.post(
        "/ingest/audio/transcribe",
        files={"file": ("empty.wav", b"", "audio/wav")},
    )
    assert res.status_code == 400


def test_文字起こしに失敗したら502にする(client: TestClient) -> None:
    with patch(
        "app.services.audio_ingest.transcribe",
        side_effect=TranscriptionError("音声認識サーバに接続できません"),
    ):
        res = client.post(
            "/ingest/audio/transcribe",
            files={"file": ("shodan.wav", b"dummy", "audio/wav")},
        )
    assert res.status_code == 502


def test_一時ファイルを残さない(client: TestClient) -> None:
    saved: list[Path] = []

    def _record(audio_path: Path) -> Transcript:
        saved.append(audio_path)
        return _TRANSCRIPT

    with patch("app.services.audio_ingest.transcribe", side_effect=_record):
        res = client.post(
            "/ingest/audio/transcribe",
            files={"file": ("shodan.wav", b"dummy", "audio/wav")},
        )
    assert res.status_code == 201
    assert saved and not saved[0].exists()


# --- 根拠の紐づけ ---


def test_音声由来なら根拠が実際の発話に紐づく(client: TestClient, db: Session) -> None:
    """抽出したナレッジの根拠が、時刻つきの本物の発話を指すこと。

    ここが合成セグメントに落ちると、根拠を「何分何秒の発話か」まで
    辿れなくなり、同じ内容がDBに二重に並ぶ。
    """
    with patch("app.services.audio_ingest.transcribe", return_value=_TRANSCRIPT):
        transcribed = client.post(
            "/ingest/audio/transcribe",
            files={"file": ("shodan.wav", b"dummy", "audio/wav")},
        ).json()

    item = ExtractedKnowledge(
        title="受注まわりから段階導入する", lesson="全社入れ替えを先に出さない"
    )
    with (
        patch(
            "app.services.extraction.extract_knowledge_with_sources",
            return_value=[(item, _TRANSCRIPT.text)],
        ),
        patch("app.services.extraction.generate_embedding", return_value=_FAKE_VECTOR),
    ):
        res = client.post(
            "/ingest/text",
            json={"raw_text": _TRANSCRIPT.text, "data_source_id": transcribed["data_source_id"]},
        )
    assert res.status_code == 201, res.text
    knowledge_id = res.json()["saved"][0]["id"]

    spans = client.get(f"/knowledge/{knowledge_id}/evidence").json()
    assert len(spans) == 1
    assert (spans[0]["start_sequence_no"], spans[0]["end_sequence_no"]) == (1, 3)
    assert [u["start_sec"] for u in spans[0]["utterances"]] == [0.0, 5.0, 9.5]

    # 合成セグメントが足されていないこと
    speakers = (
        db.execute(
            select(UtteranceSegmentTable.speaker).where(
                UtteranceSegmentTable.data_source_id == transcribed["data_source_id"]
            )
        )
        .scalars()
        .all()
    )
    assert set(speakers) == {"unknown"}


def test_本文を修正した場合は合成セグメントに退避する(client: TestClient, db: Session) -> None:
    """人が文字起こしを直すと元の発話と照合できない。

    そのときに近い発話へ無理に紐づけると、根拠として嘘になる。
    照合できないことを検知して、原文を持つセグメントを作る側に倒す。
    """
    with patch("app.services.audio_ingest.transcribe", return_value=_TRANSCRIPT):
        transcribed = client.post(
            "/ingest/audio/transcribe",
            files={"file": ("shodan.wav", b"dummy", "audio/wav")},
        ).json()

    edited = "受注のたびにエクセルにも入力しているとのことでした。"
    item = ExtractedKnowledge(title="二重入力の把握", lesson="入力経路を先に洗い出す")
    with (
        patch(
            "app.services.extraction.extract_knowledge_with_sources",
            return_value=[(item, edited)],
        ),
        patch("app.services.extraction.generate_embedding", return_value=_FAKE_VECTOR),
    ):
        res = client.post(
            "/ingest/text",
            json={"raw_text": edited, "data_source_id": transcribed["data_source_id"]},
        )
    assert res.status_code == 201, res.text

    rows = (
        db.execute(
            select(UtteranceSegmentTable)
            .where(UtteranceSegmentTable.data_source_id == transcribed["data_source_id"])
            .order_by(UtteranceSegmentTable.sequence_no)
        )
        .scalars()
        .all()
    )
    # 本物の3件のあとに、原文を持つ合成セグメントが1件だけ足される
    assert len(rows) == 4
    assert rows[-1].speaker == "source"
    assert rows[-1].sequence_no == 4
    assert rows[-1].content == edited
