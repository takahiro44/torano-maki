"""DGX Spark 上の faster-whisper への呼び出し口。

音声認識の実行場所やサーバ実装を利用側へ漏らさないため、呼び出しは
このモジュールに集約する。利用側はHTTP APIを直接呼ばず、必ず transcribe() を通す。
"""

import mimetypes
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings

DEFAULT_TIMEOUT_SECONDS = 600.0


class SttNotConfiguredError(RuntimeError):
    """設定漏れとDGXの障害を呼び出し側で区別するための例外。"""


class SttRequestError(RuntimeError):
    """DGXへの通信失敗を利用側が503等へ変換するための例外。"""


class SttResponseError(RuntimeError):
    """API契約の不一致を通信障害と区別して検知するための例外。"""


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    language: str | None = None


def transcribe(
    audio_path: Path,
    *,
    initial_prompt: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Transcript:
    """長時間音声を同期送信するため、呼び出し側は非同期ジョブ内で実行する。"""
    settings = get_settings()
    if not settings.is_stt_configured:
        raise SttNotConfiguredError("STT_BASE_URL / STT_MODEL が未設定。.env を確認すること")
    if not audio_path.is_file():
        raise FileNotFoundError(f"音声ファイルが見つかりません: {audio_path}")

    data: dict[str, str] = {
        "model": settings.stt_model,
        "language": "ja",
        "response_format": "verbose_json",
        # CPU評価で発話欠落を確認しているため、DGXでも明示的に無効化する。
        "vad_filter": "false",
    }
    if initial_prompt:
        data["initial_prompt"] = initial_prompt

    content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    try:
        with audio_path.open("rb") as audio_file:
            files = {"file": (audio_path.name, audio_file, content_type)}
            with httpx.Client(timeout=timeout) as client:
                response = client.post(settings.stt_base_url, data=data, files=files)
                response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SttRequestError(f"faster-whisper に接続できません: {exc}") from exc

    try:
        return Transcript.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise SttResponseError("faster-whisper の応答形式が不正です") from exc
