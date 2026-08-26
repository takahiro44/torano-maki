"""複数の商談文字起こしをまとめてナレッジ抽出にかける。

`run_experiment.py` は1商談ぶんを対象にした検証スクリプトで、
22件を回すには手で22回叩くことになる。**途中で1件失敗したときに
どこまで進んだのかが分からない**のが困るため、進捗と失敗を一覧で出す
ドライバをここに分ける。

`run_experiment.py` は変更しない。抽出のロジック・再試行・ID採番は
そのまま関数として呼ぶ。ここが持つのは「何件をどの順で回し、
どこへ書くか」だけに留める。検証済みの抽出処理を二重に持つと、
片方だけ直されて結果が食い違う。

出力は商談ごとに1ファイル。1つの巨大なJSONにまとめないのは、
差分レビューと再実行を商談単位でやりたいため
（1件だけ抽出をやり直したときに、他21件の差分が出ると読めない）。

使い方:
    cd experiments/knowledge-extraction
    uv run python run_batch.py
    uv run python run_batch.py --only long_001 --overwrite
    uv run python run_batch.py --concurrency 1     # DGXへの同時実行をやめる
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from run_experiment import (
    DEFAULT_ENV,
    DEFAULT_PROMPT,
    build_messages,
    extract_with_retries,
    load_env,
    load_transcript,
    materialize_result,
)

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
DEFAULT_TRANSCRIPT_DIR = _HERE / "input" / "transcripts"
DEFAULT_AUDIO_DIR = _REPO_ROOT / "tts-demo" / "output"
DEFAULT_OUTPUT_DIR = _HERE / "output" / "meetings"

# LLMの生応答の置き場。商談ごとにディレクトリを分ける。
# 同時実行すると raw_attempt_1.json が互いを上書きし、
# 失敗したときにどの商談の応答なのか分からなくなるため。
DEFAULT_RAW_DIR = _HERE / "output" / "raw"


def _audio_for(stem: str, audio_dir: Path) -> Path | None:
    """文字起こしに対応する音声を探す。

    `run_experiment.materialize_result` が occurred_at に音声の更新日時を、
    data_sources.file_name に音声のファイル名を使うため、抽出には音声の実体が要る
    （中身は読まない）。見つからない場合は、無音のダミーで代用せず飛ばす。
    商談の日時が全件同じになると、後で並べ替えたときに意味を持たなくなる。
    """
    for suffix in (".wav", ".mp3", ".m4a", ".flac"):
        candidate = audio_dir / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def extract_one(
    transcript_path: Path,
    audio_path: Path,
    output_path: Path,
    raw_dir: Path,
    base_url: str,
    model_name: str,
    max_attempts: int,
    max_tokens: int,
    timeout_seconds: float,
) -> dict[str, object]:
    transcript = load_transcript(transcript_path)
    messages = build_messages(DEFAULT_PROMPT, transcript)
    started = time.perf_counter()
    extraction = extract_with_retries(
        base_url=base_url,
        model_name=model_name,
        messages=messages,
        segment_count=len(transcript.segments),
        output_dir=raw_dir,
        max_attempts=max_attempts,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    result = materialize_result(
        transcript=transcript,
        extraction=extraction,
        transcript_path=transcript_path,
        audio_path=audio_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return {
        "knowledge": len(result.knowledge_units),
        "evidence": len(result.knowledge_evidence),
        "segments": len(result.utterance_segments),
        "seconds": time.perf_counter() - started,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript-dir", type=Path, default=DEFAULT_TRANSCRIPT_DIR)
    parser.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--base-url", help=".envのBASE_URLをこの実行だけ上書きする")
    parser.add_argument("--model-name", help=".envのMODEL_NAMEをこの実行だけ上書きする")
    parser.add_argument("--only", action="append", default=[], help="対象の名前（複数可）")
    parser.add_argument("--overwrite", action="store_true", help="既にあるものも作り直す")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--timeout", type=float, default=900)
    # 3程度ならvLLMがまとめて捌ける。増やしすぎると1件あたりが遅くなり、
    # タイムアウトで全体が巻き添えになる
    parser.add_argument("--concurrency", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env(args.env_file)
    base_url = (args.base_url or os.getenv("BASE_URL", "")).strip()
    model_name = (args.model_name or os.getenv("MODEL_NAME", "")).strip()
    if not base_url or not model_name:
        print("BASE_URL / MODEL_NAME が未設定です。.envを確認してください", file=sys.stderr)
        return 1
    if not args.transcript_dir.is_dir():
        print(f"文字起こしがありません: {args.transcript_dir}", file=sys.stderr)
        return 1

    targets: list[tuple[Path, Path, Path]] = []
    skipped: list[str] = []
    for transcript_path in sorted(args.transcript_dir.glob("*.json")):
        stem = transcript_path.stem
        if args.only and stem not in args.only:
            continue
        output_path = args.output_dir / f"{stem}.json"
        if output_path.exists() and not args.overwrite:
            skipped.append(stem)
            continue
        audio_path = _audio_for(stem, args.audio_dir)
        if audio_path is None:
            print(f"音声が見つからないので飛ばす: {stem}", file=sys.stderr)
            skipped.append(stem)
            continue
        targets.append((transcript_path, audio_path, output_path))

    if not targets:
        print(f"対象がありません（飛ばした: {len(skipped)}件）")
        return 0

    print(
        f"対象 {len(targets)}件 / model={model_name} / 同時実行={args.concurrency}",
        flush=True,
    )
    failures: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {
            pool.submit(
                extract_one,
                transcript_path,
                audio_path,
                output_path,
                args.raw_dir / output_path.stem,
                base_url,
                model_name,
                args.max_attempts,
                args.max_tokens,
                args.timeout,
            ): output_path.stem
            for transcript_path, audio_path, output_path in targets
        }
        for future in as_completed(futures):
            stem = futures[future]
            done += 1
            try:
                stats = future.result()
            except Exception as error:  # noqa: BLE001 - 1件の失敗で全体を止めない
                print(f"[{done}/{len(targets)}] {stem}: 失敗 {error}", flush=True)
                failures.append(stem)
                continue
            print(
                f"[{done}/{len(targets)}] {stem}: "
                f"ナレッジ{stats['knowledge']}件 / 根拠{stats['evidence']}件 / "
                f"{stats['seconds']:.0f}秒",
                flush=True,
            )

    if failures:
        print(f"\n失敗 {len(failures)}件: {', '.join(failures)}", file=sys.stderr)
        print("--only で失敗したものだけ流し直せます", file=sys.stderr)
        return 1
    print(f"\n完了: {len(targets)}件 -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
