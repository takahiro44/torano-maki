"""音声認識。

音声認識ライブラリは差し替えの可能性が高いため、
呼び出し口をこの1関数に集約する（CLAUDE.md 6章）。
利用側はHTTPクライアントを直接触らず、必ず transcribe() を通すこと。
そうしておけば、DGX上のサーバをやめて各自のPCで faster-whisper を
直接動かす形に戻す場合でも、このファイルだけ差し替えれば済む。

現在の実装は DGX Spark 上の faster-whisper サーバ（OpenAI互換）を叩く。
モデルと設定の根拠は experiments/stt/README.md を参照。
"""

import logging
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    text: str
    segments: list[TranscriptSegment] = []
    language: str | None = None


class SttNotConfiguredError(RuntimeError):
    """STT_BASE_URL が空のときに上げる。"""


class TranscriptionError(RuntimeError):
    """音声認識サーバへの到達・応答に失敗したときに上げる。"""


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

# VADは無効にする。**有効にすると発話が丸ごと消える。**
# 実測で14秒分（41字）が欠落し、しかも消えたのは「納期を延ばしてもらったり
# 代替品を提案したり」という営業ナレッジとして最も価値のある一文だった。
# 欠落は後段のLLMでは直せない（気づかず自然な文で埋めてしまう）ため、
# 無音が多少残る方が安全。詳細は experiments/stt/README.md「VADは切ること」。
_VAD_FILTER = False

# 音声は日本語で固定する。自動判定に任せると、冒頭が相槌だけの商談で
# 英語と誤判定されることがあり、その場合は全文が英訳されて返る。
_LANGUAGE = "ja"

# 接続は即座に失敗してほしいが、推論は待つ必要がある。
# read が長いのは推論時間そのもの（実測: 8分50秒の音声で31秒）。
# write が長いのは音声ファイルの送信にかかる時間（数十MBになる）。
_TIMEOUT = httpx.Timeout(connect=5.0, write=120.0, read=600.0, pool=5.0)


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


def transcribe(audio_path: Path) -> Transcript:
    """音声ファイルを文字起こしする。

    設定と実装の根拠は experiments/stt/README.md を参照。
    """
    settings = get_settings()
    if not settings.is_stt_configured:
        raise SttNotConfiguredError("STT_BASE_URL が未設定。.env を確認すること")

    url = _transcriptions_url(settings.stt_base_url)
    data = {
        "language": _LANGUAGE,
        "response_format": "verbose_json",
        "vad_filter": str(_VAD_FILTER).lower(),
        "initial_prompt": GLOSSARY,
    }

    try:
        with audio_path.open("rb") as f:
            files = {"file": (audio_path.name, f, "application/octet-stream")}
            with httpx.Client(timeout=_TIMEOUT) as client:
                response = client.post(url, data=data, files=files)
                response.raise_for_status()
                body = response.json()
    except httpx.HTTPError as exc:
        logger.exception("音声認識サーバへのリクエストに失敗しました: %s", url)
        raise TranscriptionError(f"音声認識サーバに接続できません: {exc}") from exc
    except OSError as exc:
        raise TranscriptionError(f"音声ファイルを読めません: {exc}") from exc

    return _parse_response(body)


def _parse_response(body: object) -> Transcript:
    """サーバの応答を Transcript にする。

    verbose_json の形が変わっても、ここだけ直せば済むように切り出している。
    """
    if not isinstance(body, dict):
        raise TranscriptionError(f"音声認識サーバの応答形式が想定外です: {type(body).__name__}")

    raw_segments = body.get("segments") or []
    segments: list[TranscriptSegment] = []
    for seg in raw_segments:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                start=float(seg.get("start") or 0.0),
                end=float(seg.get("end") or 0.0),
                text=text,
            )
        )

    # text が空でも segments から復元できる。逆は復元できないので segments を正とする
    text = str(body.get("text") or "").strip()
    if not text:
        text = "".join(s.text for s in segments)

    if not text:
        raise TranscriptionError("文字起こし結果が空でした。無音の音声である可能性があります")

    language = body.get("language")
    return Transcript(
        text=text,
        segments=segments,
        language=str(language) if language else None,
    )
