"""台本JSONを渡すと音声ファイルまで一気に作る。取り込み → 合成 → 結合。

ChatGPT が書いた台本を検証して `scripts/` に置き、そのまま合成して
`output/<台本名>.wav` を出すところまでを1コマンドにする。
台本が増えるたびに2つのコマンドを順番に打ち、途中で失敗したら
どこまで進んだか思い出す、という手順を無くすのが目的。

合成の実体は generate_tts.py にある。ここは手順をつなぐだけで、
音声の作り方（プロンプト・分割・結合・音量調整）は持たない。
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from generate_tts import (
    GAP_MS,
    MAX_INPUT_BYTES,
    SAFETY_MARGIN_BYTES,
    Script,
    concat_wavs,
    duration_seconds,
    generate_chunked,
    generate_per_turn,
    load_script,
)
from google.cloud import texttospeech
from import_script import DraftError, collect_inputs, import_draft

# 途中まで作った part を再利用してよいかの判断に使う。
# 台本を書き直したのに古い part が混ざると、**音声だけ古いまま**気づけない
STAMP_NAME = "script.sha256"


def stamp_value(script_path: Path, mode: str, limit: int | None) -> str:
    """part の作り直しが要るかを決める指紋。台本・モード・発話数で変わる。"""
    payload = script_path.read_bytes() + f"|{mode}|{limit}".encode()
    return hashlib.sha256(payload).hexdigest()


def reusable_parts(script: Script, stamp: str) -> bool:
    """前回と同じ条件で作った part が残っているか。"""
    stamp_path = script.output_dir / STAMP_NAME
    if not stamp_path.exists():
        return False
    return stamp_path.read_text(encoding="utf-8").strip() == stamp


def build(
    draft_path: Path,
    *,
    mode: str,
    limit: int | None,
    chunk_bytes: int,
    normalize: bool,
    force_script: bool,
    force_audio: bool,
    dry_run: bool,
    only: list[str] | None = None,
    prefix: str = "",
) -> list[Path]:
    """下書き1ファイルを音声にする。**束なら入っている本数ぶん作る。**

    途中の1本で落ちても、そこまでに出来た音声は残る。作れたものを返す。
    """
    print(f"検証中: {draft_path}")
    script_paths = import_draft(
        draft_path, force=force_script, only=only, prefix=prefix
    )

    outputs: list[Path] = []
    for script_path in script_paths:
        outputs.append(
            synthesize_script(
                script_path,
                mode=mode,
                limit=limit,
                chunk_bytes=chunk_bytes,
                normalize=normalize,
                force_audio=force_audio,
                dry_run=dry_run,
            )
        )
    return [path for path in outputs if path is not None]


def synthesize_script(
    script_path: Path,
    *,
    mode: str,
    limit: int | None,
    chunk_bytes: int,
    normalize: bool,
    force_audio: bool,
    dry_run: bool,
) -> Path | None:
    """取り込み済みの台本1本を音声にする。`--dry-run` のときは None を返す。"""
    script = load_script(script_path, limit=limit)
    script.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"台本: {script.title}（{script.stem}）")

    if dry_run:
        print("  --dry-run のため合成は行わない")
        return None

    # 指紋は合成の前に置く。途中で失敗しても、次回そこから再開できるようにするため
    stamp = stamp_value(script_path, mode, limit)
    resume = mode == "per-turn" and not force_audio and reusable_parts(script, stamp)
    if resume:
        print("  前回と同じ台本のため、既にある part は作り直さない")
    (script.output_dir / STAMP_NAME).write_text(stamp, encoding="utf-8")

    client = texttospeech.TextToSpeechClient()
    if mode == "per-turn":
        parts = generate_per_turn(client, script, resume=resume)
    else:
        parts = generate_chunked(client, script, chunk_bytes)

    concat_wavs(parts, script.default_output, GAP_MS, normalize=normalize)
    print(
        f"生成完了: {script.default_output} "
        f"({duration_seconds(script.default_output):.1f} 秒)"
    )
    return script.default_output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="台本JSONから音声ファイルまでを一括で作る"
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="下書きJSON。ファイル / ディレクトリ / drafts/ 配下の名前",
    )
    parser.add_argument(
        "--mode",
        choices=("per-turn", "chunk"),
        default="per-turn",
        help="per-turn: 1発話ずつ合成して声を固定する（推奨） / chunk: まとめて合成",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="先頭N発話だけ生成する（動作確認用）"
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="名前",
        help="束のうち指定した名前の台本だけを音声にする",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="台本と音声の名前の先頭に付ける文字列（例 long_ → long_001.wav）",
    )
    parser.add_argument(
        "--chunk-bytes",
        type=int,
        default=MAX_INPUT_BYTES - SAFETY_MARGIN_BYTES,
        help=f"chunkモードの1リクエスト上限byte数（APIの上限は {MAX_INPUT_BYTES}）",
    )
    parser.add_argument(
        "--no-normalize", action="store_true", help="パート間の音量調整を行わない"
    )
    parser.add_argument(
        "--force-script",
        action="store_true",
        help="scripts/ にある同名の台本を上書きする",
    )
    parser.add_argument(
        "--force-audio",
        action="store_true",
        help="台本が変わっていなくても part を作り直す（APIを呼び直す）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="取り込みと検証だけ行い、合成しない（APIを呼ばない）",
    )
    args = parser.parse_args()

    if args.chunk_bytes > MAX_INPUT_BYTES:
        raise SystemExit(f"--chunk-bytes は {MAX_INPUT_BYTES} 以下にすること")

    outputs: list[Path] = []
    for draft_path in collect_inputs(args.inputs):
        try:
            outputs.extend(
                build(
                    draft_path,
                    mode=args.mode,
                    limit=args.limit,
                    chunk_bytes=args.chunk_bytes,
                    normalize=not args.no_normalize,
                    force_script=args.force_script,
                    force_audio=args.force_audio,
                    dry_run=args.dry_run,
                    only=args.only,
                    prefix=args.prefix,
                )
            )
        except DraftError as error:
            raise SystemExit(f"  ✗ {error}") from error

    # 何本も作ると途中の行が流れるため、最後にまとめて出す
    if len(outputs) > 1:
        print(f"\n{len(outputs)} 本の音声を作った")
        for path in outputs:
            print(f"  {path}")


if __name__ == "__main__":
    main()
