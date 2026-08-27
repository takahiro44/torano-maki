"""音声取り込みのテスト。DGXへ実データを送らずに検証する。

前半はSTTクライアントのAPI契約（どんなリクエストを組み立て、どんな応答を
どの例外に変換するか）、後半はエンドポイントと根拠の紐づけ。
ネットワークに出るところは必ずモックする。DGXが落ちていると
自分の手元もCIも止まる状態にしないため。
"""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.health import health_config
from app.config import Settings
from app.models.knowledge import ExtractedKnowledge
from app.models.tables import DataSourceTable, UtteranceSegmentTable
from app.services import transcription
from app.services.transcription import (
    EmptyTranscriptError,
    SttNotConfiguredError,
    SttRequestError,
    SttResponseError,
    Transcript,
    TranscriptSegment,
    _parse_response,
    _transcriptions_url,
    transcribe,
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


# ============================================================================
# STTクライアントの契約
# ============================================================================


class _FakeResponse:
    def __init__(self, body: object, *, status_error: bool = False) -> None:
        self._body = body
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error:
            request = httpx.Request("POST", "http://dgx/stt")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("unavailable", request=request, response=response)

    def json(self) -> object:
        return self._body


class _FakeClient:
    response = _FakeResponse({})
    last_request: dict[str, Any] = {}

    def __init__(self, *, timeout: httpx.Timeout) -> None:
        self.timeout = timeout

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        type(self).last_request = {"url": url, "timeout": self.timeout, **kwargs}
        return type(self).response


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.wav"
    path.write_bytes(b"RIFF-test")
    return path


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.response = _FakeResponse(
        {
            "text": "価格ではなく保守体制を確認します。",
            "segments": [{"start": 0.0, "end": 2.5, "text": "価格ではなく保守体制を確認します。"}],
            "language": "ja",
        }
    )
    _FakeClient.last_request = {}
    monkeypatch.setattr(transcription.httpx, "Client", _FakeClient)
    monkeypatch.setattr(
        transcription,
        "get_settings",
        lambda: Settings(stt_base_url="http://dgx:8082/v1/audio/transcriptions"),
    )


def test_DGXへOpenAI互換形式で音声を送る(fake_client: None, audio_file: Path) -> None:
    result = transcribe(audio_file, initial_prompt="大塚商会、納期", timeout=12.0)

    assert result.text == "価格ではなく保守体制を確認します。"
    assert result.segments[0].end == 2.5
    request = _FakeClient.last_request
    assert request["url"] == "http://dgx:8082/v1/audio/transcriptions"
    # 接続だけは短く、推論は指定どおり待つ
    assert request["timeout"].read == 12.0
    assert request["timeout"].connect == 5.0
    assert request["data"] == {
        "model": "medium",
        "language": "ja",
        "response_format": "verbose_json",
        "vad_filter": "false",
        "initial_prompt": "大塚商会、納期",
    }
    assert request["files"]["file"][0] == "sample.wav"


def test_用語集は既定で渡される(fake_client: None, audio_file: Path) -> None:
    """渡し忘れると業務用語が取れなくなるが、エラーにはならず気づけない。

    実測で用語集なしでは FAX と Excel が1つも取れなかった。
    呼び出し側が意識しなくてよい形に閉じ込めておく（CLAUDE.md 3.3 と同じ理由）。
    """
    transcribe(audio_file)
    assert _FakeClient.last_request["data"]["initial_prompt"] == transcription.GLOSSARY


def test_用語集は明示的に外せる(fake_client: None, audio_file: Path) -> None:
    # 用語集の効果を測り直したいときのため
    transcribe(audio_file, initial_prompt=None)
    assert "initial_prompt" not in _FakeClient.last_request["data"]


def test_STT設定が無ければ通信しない(
    fake_client: None, audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcription, "get_settings", lambda: Settings(stt_base_url=""))

    with pytest.raises(SttNotConfiguredError, match="STT_BASE_URL"):
        transcribe(audio_file)
    assert _FakeClient.last_request == {}


def test_音声ファイルが無ければ通信しない(fake_client: None, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="音声ファイル"):
        transcribe(tmp_path / "missing.wav")
    assert _FakeClient.last_request == {}


def test_DGXのHTTPエラーを専用例外にする(fake_client: None, audio_file: Path) -> None:
    _FakeClient.response = _FakeResponse({}, status_error=True)

    with pytest.raises(SttRequestError, match="faster-whisper"):
        transcribe(audio_file)


def test_DGXの不正な応答を専用例外にする(fake_client: None, audio_file: Path) -> None:
    _FakeClient.response = _FakeResponse({"unexpected": True})

    with pytest.raises(SttResponseError, match="応答形式"):
        transcribe(audio_file)


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


def test_セグメントの前後の空白を落とす() -> None:
    # text は segments の連結と一致していなければならない。
    # ずれると根拠を文字オフセットで引けなくなる（_SegmentLocator）
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
    assert result.text == "".join(s.text for s in result.segments)
    assert [s.text for s in result.segments] == ["あいう", "えお"]
    assert result.language == "ja"


def test_textが空でもセグメントから復元する() -> None:
    result = _parse_response(
        {"text": "", "segments": [{"start": 0.0, "end": 1.0, "text": "こんにちは"}]}
    )
    assert result.text == "こんにちは"


def test_無音で結果が空なら専用の例外にする() -> None:
    # サーバ障害と混ぜると、無音のファイルを投げた人がDGXを疑って調べ始める
    with pytest.raises(EmptyTranscriptError):
        _parse_response({"text": "", "segments": []})


def test_ヘルスチェックでSTT設定を確認できる() -> None:
    result = health_config(
        Settings(
            stt_base_url="http://dgx:8082/v1/audio/transcriptions",
            stt_model="medium",
        )
    )

    assert result.stt_configured is True
    assert result.stt_base_url == "http://dgx:8082/v1/audio/transcriptions"
    assert result.stt_model == "medium"


# ============================================================================
# エンドポイント
# ============================================================================


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


def test_DGXに繋がらなければ502にする(client: TestClient) -> None:
    with patch(
        "app.services.audio_ingest.transcribe",
        side_effect=SttRequestError("faster-whisper に接続できません"),
    ):
        res = client.post(
            "/ingest/audio/transcribe",
            files={"file": ("shodan.wav", b"dummy", "audio/wav")},
        )
    assert res.status_code == 502


def test_無音の音声は400にする(client: TestClient) -> None:
    # 音声側の問題をサーバ障害として返すと、原因の切り分けを誤らせる
    with patch(
        "app.services.audio_ingest.transcribe",
        side_effect=EmptyTranscriptError("文字起こし結果が空でした"),
    ):
        res = client.post(
            "/ingest/audio/transcribe",
            files={"file": ("silent.wav", b"dummy", "audio/wav")},
        )
    assert res.status_code == 400


def test_STT未設定なら503にする(client: TestClient) -> None:
    with patch(
        "app.services.audio_ingest.transcribe",
        side_effect=SttNotConfiguredError("STT_BASE_URL / STT_MODEL が未設定"),
    ):
        res = client.post(
            "/ingest/audio/transcribe",
            files={"file": ("shodan.wav", b"dummy", "audio/wav")},
        )
    assert res.status_code == 503


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


# ============================================================================
# 根拠の紐づけ
# ============================================================================


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
        title="受注まわりから段階導入する",
        lesson="全社入れ替えを先に出さない",
        knowledge_type="business",
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
    item = ExtractedKnowledge(
        title="二重入力の把握", lesson="入力経路を先に洗い出す", knowledge_type="business"
    )
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
