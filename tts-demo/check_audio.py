"""生成した音声を機械的に点検する。**20本を全部聴く前の足切り。**

台本の文字数と音声の長さを突き合わせれば、聴かなくても異常は見つかる。
実際にこれで「本文ではなくプロンプトを読み上げていたパート」を1件見つけた
（44字の発話が27.7秒あった。詳細は docs/setup-notes.md）。

見るのは4つ。
- パート数が台本の発話数と合っているか（合成の取りこぼし）
- 無音・極端に短いパートが無いか（合成は成功したが中身が空）
- 文字数に対して音声が短すぎ／長すぎないか（読み飛ばし・繰り返し・プロンプト読み上げ）
- 全体の話速

**引っかかったものが必ず不良とは限らない。** 列挙や言い淀みで3字/秒まで落ちる。
中身まで確かめるには `experiments/stt` の環境で文字起こしする。

    uv run check_audio.py              # scripts/ にある全台本
    uv run check_audio.py long_019     # 指定した台本だけ
"""

from __future__ import annotations

import array
import json
import math
import sys
import wave
from pathlib import Path

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
OUTPUT_ROOT = BASE_DIR / "output"

# 日本語の朗読はおおむね1秒あたり4〜6文字。列挙や言い淀みがあると3字/秒まで落ちる。
# 文字起こしで実測したところ 3.2字/秒 でも中身は正しかったため、下限は 2.5 に取る
MIN_CHARS_PER_SEC = 2.5
MAX_CHARS_PER_SEC = 12.0

# これより短いパートは、合成が空振りした可能性がある
MIN_PART_SECONDS = 0.4


def duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def rms(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
    samples = array.array("h")
    samples.frombytes(frames)
    if not samples:
        return 0.0
    return math.sqrt(sum(float(s) * s for s in samples) / len(samples))


def check(stem: str) -> list[str]:
    """1本ぶんを点検し、気づいた点を返す。問題が無ければ空。"""
    problems: list[str] = []
    script = json.loads((SCRIPTS_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    turns = script["turns"]
    final = OUTPUT_ROOT / f"{stem}.wav"
    parts = sorted((OUTPUT_ROOT / stem).glob("part_*.wav"))

    if not final.exists():
        return [f"{stem}: 音声が無い"]
    if len(parts) != len(turns):
        problems.append(f"{stem}: パート {len(parts)} 本 ≠ 発話 {len(turns)} 本")

    for part, turn in zip(parts, turns, strict=False):
        seconds = duration(part)
        chars = len(turn["text"])
        if seconds < MIN_PART_SECONDS:
            problems.append(f"{stem}/{part.name}: {seconds:.2f}秒しかない")
            continue
        rate = chars / seconds
        if rate < MIN_CHARS_PER_SEC:
            problems.append(
                f"{stem}/{part.name}: 間延びしている（{chars}字 / {seconds:.1f}秒）"
            )
        elif rate > MAX_CHARS_PER_SEC:
            problems.append(
                f"{stem}/{part.name}: 早口すぎる（{chars}字 / {seconds:.1f}秒）"
            )
        if rms(part) < 100:
            problems.append(f"{stem}/{part.name}: ほぼ無音")

    total_chars = sum(len(t["text"]) for t in turns)
    seconds = duration(final)
    print(
        f"{stem}\t{seconds / 60:5.1f}分\t{len(parts):3d}パート\t"
        f"{total_chars:5d}字\t{total_chars / seconds:4.1f}字/秒\t"
        f"{'⚠ ' + str(len(problems)) + '件' if problems else 'OK'}"
    )
    return problems


def main() -> None:
    stems = sys.argv[1:] or [
        path.stem
        for path in sorted(SCRIPTS_DIR.glob("*.json"))
        if (OUTPUT_ROOT / f"{path.stem}.wav").exists()
    ]
    all_problems: list[str] = []
    for stem in stems:
        all_problems.extend(check(stem))

    print()
    if all_problems:
        print(f"気になる点 {len(all_problems)} 件")
        for problem in all_problems:
            print(f"  {problem}")
    else:
        print("全て問題なし")


if __name__ == "__main__":
    main()
