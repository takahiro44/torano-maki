"""DGXへ実データを送らず、STTクライアントのAPI契約を検証する。"""

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.api.health import health_config
from app.config import Settings
from app.services import transcription
from app.services.transcription import (
    SttNotConfiguredError,
    SttRequestError,
    SttResponseError,
    transcribe,
)


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

    def __init__(self, *, timeout: float) -> None:
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


@pytest.fixture(autouse=True)
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


def test_DGXへOpenAI互換形式で音声を送る(audio_file: Path) -> None:
    result = transcribe(audio_file, initial_prompt="大塚商会、納期", timeout=12.0)

    assert result.text == "価格ではなく保守体制を確認します。"
    assert result.segments[0].end == 2.5
    request = _FakeClient.last_request
    assert request["url"] == "http://dgx:8082/v1/audio/transcriptions"
    assert request["timeout"] == 12.0
    assert request["data"] == {
        "model": "medium",
        "language": "ja",
        "response_format": "verbose_json",
        "vad_filter": "false",
        "initial_prompt": "大塚商会、納期",
    }
    assert request["files"]["file"][0] == "sample.wav"


def test_STT設定が無ければ通信しない(audio_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcription, "get_settings", lambda: Settings(stt_base_url=""))

    with pytest.raises(SttNotConfiguredError, match="STT_BASE_URL"):
        transcribe(audio_file)
    assert _FakeClient.last_request == {}


def test_音声ファイルが無ければ通信しない(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="音声ファイル"):
        transcribe(tmp_path / "missing.wav")
    assert _FakeClient.last_request == {}


def test_DGXのHTTPエラーを専用例外にする(audio_file: Path) -> None:
    _FakeClient.response = _FakeResponse({}, status_error=True)

    with pytest.raises(SttRequestError, match="faster-whisper"):
        transcribe(audio_file)


def test_DGXの不正な応答を専用例外にする(audio_file: Path) -> None:
    _FakeClient.response = _FakeResponse({"unexpected": True})

    with pytest.raises(SttResponseError, match="応答形式"):
        transcribe(audio_file)


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
