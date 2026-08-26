"""商談音声をまとめて文字起こしし、抽出の入力となるJSONを書き出す。

**なぜ必要か。**
`experiments/knowledge-extraction/` は文字起こし済みJSONを入力に取るが、
その1件（`medium_glossary.json`）は手作業で置かれたものだった。
商談を22件に増やすにあたり、同じことを22回手でやると、
**誰がどの設定で文字起こししたのか分からないJSONがリポジトリに並ぶ。**
設定を固定して一括で回すために、この経路を用意する。

文字起こしは `app.services.transcription.transcribe()` を通す。
HTTPを直接叩かないのは、用語集とVAD無効化がその中に閉じ込められているため
（transcription.py の docstring / CLAUDE.md 3.3）。ここで自前のリクエストを
組むと、本番と違う設定で作ったデータが検証の入力になる。

出力は `experiments/knowledge-extraction/` の `TranscriptDocument` が
そのまま読める形にしてある（`meta` は extra="ignore" で読み飛ばされる）。

使い方:
    cd backend
    uv run python scripts/transcribe_meetings.py
    uv run python scripts/transcribe_meetings.py --only long_001 --overwrite
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.config import get_settings
from app.services.transcription import (
    GLOSSARY,
    EmptyTranscriptError,
    SttNotConfiguredError,
    SttRequestError,
    SttResponseError,
    Transcript,
    transcribe,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIO_DIR = _REPO_ROOT / "tts-demo" / "output"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "experiments" / "knowledge-extraction" / "input" / "transcripts"

# 音声の拡張子。ingest.py の _ALLOWED_SUFFIXES と揃えている
_AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".flac")


def _usable_segments(transcript: Transcript) -> tuple[list[dict[str, object]], int]:
    """長さゼロのセグメントを落とす。

    抽出側の `TranscriptSegment` は `end > start` を必須にしているため、
    ここで落としておかないと22件のうち1件が後段のバリデーションで落ち、
    原因がDGXなのかスキーマなのか分からなくなる。
    落とした件数は呼び出し側で必ず表示する（黙って減らさない）。
    """
    segments: list[dict[str, object]] = []
    dropped = 0
    for segment in transcript.segments:
        if segment.end <= segment.start or not segment.text:
            dropped += 1
            continue
        segments.append({"start": segment.start, "end": segment.end, "text": segment.text})
    return segments, dropped


def transcribe_one(audio_path: Path, output_path: Path) -> dict[str, object]:
    started = time.perf_counter()
    transcript = transcribe(audio_path)
    elapsed = time.perf_counter() - started

    segments, dropped = _usable_segments(transcript)
    if not segments:
        raise EmptyTranscriptError(f"使えるセグメントがありません: {audio_path.name}")

    settings = get_settings()
    # どの音声をどの設定で文字起こししたのかをJSON自身に持たせる。
    # 22件が並ぶと、後から「これは用語集ありか」を思い出せないため
    # （experiments/stt/test_stt.py が meta を持つのと同じ理由）。
    document = {
        "text": "".join(str(s["text"]) for s in segments),
        "segments": segments,
        "language": transcript.language,
        "meta": {
            "audio": audio_path.name,
            "model": settings.stt_model,
            "device": "dgx",
            "vad": False,
            "glossary": GLOSSARY,
            "transcribe_seconds": round(elapsed, 1),
            "dropped_segments": dropped,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"segments": len(segments), "dropped": dropped, "seconds": elapsed}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--only", action="append", default=[], help="対象の拡張子なしファイル名（複数可）"
    )
    parser.add_argument("--overwrite", action="store_true", help="既にJSONがあるものも作り直す")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.audio_dir.is_dir():
        print(f"音声ディレクトリがありません: {args.audio_dir}", file=sys.stderr)
        return 1

    audio_files = sorted(
        path
        for path in args.audio_dir.iterdir()
        if path.suffix.lower() in _AUDIO_SUFFIXES and (not args.only or path.stem in args.only)
    )
    if not audio_files:
        print(f"対象の音声がありません: {args.audio_dir}", file=sys.stderr)
        return 1

    print(f"対象 {len(audio_files)}件 / 出力先 {args.output_dir}", flush=True)
    failures: list[str] = []
    for index, audio_path in enumerate(audio_files, start=1):
        output_path = args.output_dir / f"{audio_path.stem}.json"
        if output_path.exists() and not args.overwrite:
            print(f"[{index}/{len(audio_files)}] {audio_path.name}: 既にあるので飛ばす")
            continue
        print(f"[{index}/{len(audio_files)}] {audio_path.name} …", end="", flush=True)
        try:
            stats = transcribe_one(audio_path, output_path)
        except (
            SttNotConfiguredError,
            SttRequestError,
            SttResponseError,
            EmptyTranscriptError,
            FileNotFoundError,
        ) as error:
            # 1件失敗しても残りを進める。22件を頭から流し直すのは高くつく
            print(f" 失敗: {error}", flush=True)
            failures.append(audio_path.name)
            continue
        dropped = f" / 除外{stats['dropped']}" if stats["dropped"] else ""
        print(
            f" {stats['segments']}セグメント{dropped} / {stats['seconds']:.1f}秒",
            flush=True,
        )

    if failures:
        print(f"\n失敗 {len(failures)}件: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("\n完了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
