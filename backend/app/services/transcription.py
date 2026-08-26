"""DGX Spark 上の faster-whisper への呼び出し口。

音声認識の実行場所やサーバ実装を利用側へ漏らさないため、呼び出しは
このモジュールに集約する。利用側はHTTP APIを直接呼ばず、必ず transcribe() を通す。
そうしておけば、DGXをやめて各自のPCで faster-whisper を直接動かす形に
戻す場合でも、このファイルだけ差し替えれば済む（CLAUDE.md 6章）。

モデルと設定の根拠は experiments/stt/README.md を参照。
"""

import mimetypes
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings

# 推論そのものにかかる時間の上限（実測: 8分50秒の音声で31秒）。
DEFAULT_TIMEOUT_SECONDS = 600.0

# Whisper に事前に渡す語彙。製品名や業務用語は音声だけからは正しい表記に
# たどり着けないため（`納期`→脳記、`代替`→大体 になる）先に与えて表記を寄せる。
#
# experiments/stt の検証で、これを渡した medium が CER 3.1% と最良だった。
# 業務用語（納期・代替・新人・FAX・Excel）を全て正しく取れた唯一の構成でもある。
#
# 本来は製品マスタ・顧客マスタから組み立てるべきもの。デモ範囲ではそれが無いため
# 固定値を置いている。増やす場合は experiments/stt で効果を測ってからにすること
# （用語集は長くすると逆に精度が落ちる）。
GLOSSARY = (
    "大塚商会、SMILE V 2nd Edition、販売管理システム、基幹システム、"
    "受注、発注、納期、代替品、在庫、見積、営業事務、FAX、Excel"
)


class SttNotConfiguredError(RuntimeError):
    """設定漏れとDGXの障害を呼び出し側で区別するための例外。"""


class SttRequestError(RuntimeError):
    """DGXへの通信失敗を利用側が502等へ変換するための例外。"""


class SttResponseError(RuntimeError):
    """API契約の不一致を通信障害と区別して検知するための例外。"""


class EmptyTranscriptError(RuntimeError):
    """文字起こし結果が空だったときに上げる。

    サーバ側の障害ではなく**送られた音声の問題**なので、
    SttResponseError と分けている。混ぜると、無音のファイルを投げた人が
    DGXを疑って調べ始めることになる。
    """


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    language: str | None = None


def _transcriptions_url(base_url: str) -> str:
    """STT_BASE_URL からエンドポイントのURLを組み立てる。

    .env に「/v1 まで」と「/v1/audio/transcriptions まで」のどちらが
    書かれていても動くようにするために存在する。
    BASE_URL（vLLM）が /v1 までの慣習なので前者と書き間違えやすく、
    間違えると 404 や 405 になって原因が分かりにくい。
    """
    url = base_url.rstrip("/")
    if url.endswith("/audio/transcriptions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/audio/transcriptions"
    return f"{url}/v1/audio/transcriptions"


def _build_timeout(read_seconds: float) -> httpx.Timeout:
    """接続だけは短く、推論と送信は長く待つ。

    全体を1つの値にすると、STT_BASE_URL のIPを間違えたときに
    到達しないアドレスへ read と同じ時間（既定10分）待つことになる。
    write が長いのは音声ファイルの送信にかかる時間（数十MBになる）。
    """
    return httpx.Timeout(connect=5.0, write=120.0, read=read_seconds, pool=5.0)


def transcribe(
    audio_path: Path,
    *,
    initial_prompt: str | None = GLOSSARY,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Transcript:
    """音声ファイルを文字起こしする。

    **既定で用語集を渡す。** 渡さなくてもエラーにはならないが、
    業務用語（納期・代替・FAX・Excel）が取れなくなる。
    埋め込みの e5 プレフィックスと同じで、呼び出し側が意識しなくてよい形に
    閉じ込めておかないと、忘れても気づけない（CLAUDE.md 3.3 と同じ理由）。
    用語集を外して比較したい場合だけ initial_prompt=None を渡すこと。
    """
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

    url = _transcriptions_url(settings.stt_base_url)
    content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    try:
        with audio_path.open("rb") as audio_file:
            files = {"file": (audio_path.name, audio_file, content_type)}
            with httpx.Client(timeout=_build_timeout(timeout)) as client:
                response = client.post(url, data=data, files=files)
                response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SttRequestError(f"faster-whisper に接続できません: {exc}") from exc

    return _parse_response(response.json())


def _parse_response(payload: object) -> Transcript:
    """応答を Transcript にする。

    **`text` は必ず segments の連結と一致させる。** ナレッジの根拠は
    連結文字列上の文字オフセットで発話に対応づけているため（extraction.py の
    _SegmentLocator）、ここがずれると根拠が引けなくなる。
    """
    try:
        transcript = Transcript.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        raise SttResponseError("faster-whisper の応答形式が不正です") from exc

    segments = [
        segment.model_copy(update={"text": segment.text.strip()})
        for segment in transcript.segments
        if segment.text.strip()
    ]
    # text が空でも segments からは復元できる。逆は復元できないので segments を正とする
    text = transcript.text.strip() or "".join(segment.text for segment in segments)
    if not text:
        raise EmptyTranscriptError("文字起こし結果が空でした。無音の音声である可能性があります")
    return transcript.model_copy(update={"text": text, "segments": segments})
