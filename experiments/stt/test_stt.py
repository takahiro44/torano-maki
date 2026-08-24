"""faster-whisper をCPUで動かし、モデルサイズごとの精度と所要時間を測る。

DGXを使わずノートPCのCPUで実用になるかを判断するための検証（docs/decisions.md
「音声認識の候補と注意点」）。判断に必要なのは「どのサイズなら商談音声の
文字起こしが実用に足りるか」なので、精度（CER）と所要時間を必ず並べて出す。

出力JSONは backend/app/services/transcription.py の Transcript と同じ形にしてある。
本実装へ移すときに変換を挟まなくて済むようにするため。
"""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
from pathlib import Path

from faster_whisper import WhisperModel

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]

# per-turn モードで合成したもの。1発話ごとにリクエストを分けてプリセット音声を
# 名前で固定しているため、途中で話者の声が入れ替わらない。
# 声が揺れる音声で測ると、文字起こしの精度なのか合成の乱れなのか切り分けられない。
DEFAULT_AUDIO = _REPO_ROOT / "tts-demo" / "output" / "sales_demo_perturn.wav"
DEFAULT_REFERENCE = _REPO_ROOT / "tts-demo" / "dialogue.json"

# Whisper に事前に渡す語彙。製品名や業務用語は音声だけからは正しい表記に
# たどり着けないため（SMILE V 2nd Edition が「スマイル」になる）、
# 先に与えて表記を寄せられるかを見る。
#
# 本番では製品マスタや顧客マスタから組み立てる想定。ここではそれを模した固定値。
# **台本の文そのものは入れない。** 入れると答えを教えることになり検証にならない。
GLOSSARY = (
    "大塚商会、SMILE V 2nd Edition、販売管理システム、基幹システム、"
    "受注、発注、納期、代替品、在庫、見積、営業事務、FAX、Excel"
)

# CERは表記ゆれ（句読点・全半角・空白）を誤りとして数えたくないため、
# 比較前に落とす。残るのはかな・漢字・英数字の並びだけになる。
_IGNORED_CHARS = set("、。，．・！？!?「」『』（）()〜~ー-…　 \t\n")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(c for c in text if c not in _IGNORED_CHARS)


def char_error_rate(reference: str, hypothesis: str) -> float:
    """文字誤り率。1.0 に近いほど悪い。

    jiwer などを足すと依存が増えるので、レーベンシュタイン距離を直接書く。
    数千文字なら素のPythonでも数秒で終わる。
    """
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i]
        for j, h in enumerate(hyp, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def load_reference(path: Path) -> str:
    """TTSの台本を正解テキストとして使う。

    人手で書き起こした正解を用意する時間がないが、この音声は台本から
    合成したものなので、台本がそのまま正解になる。
    """
    turns = json.loads(path.read_text(encoding="utf-8"))
    return "".join(turn["text"] for turn in turns)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", default="small", help="tiny/base/small/medium/large-v3 など"
    )
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument(
        "--compute-type", default="int8", help="int8 / int8_float32 / float32"
    )
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument(
        "--threads", type=int, default=0, help="0でfaster-whisperの既定"
    )
    # VADは既定でoff。medium + VADで14秒分の発話が丸ごと欠落する事象を実測したため
    # （README「CERだけで判断してはいけない」を参照）
    parser.add_argument("--vad", action="store_true", help="無音区間の除去を有効にする")
    parser.add_argument("--no-reference", action="store_true", help="CERを計測しない")
    parser.add_argument("--tag", default="", help="出力ファイル名に付ける識別子")
    parser.add_argument(
        "--glossary",
        action="store_true",
        help="GLOSSARY を initial_prompt として渡す（用語集の効果を見る）",
    )
    args = parser.parse_args()

    if not args.audio.exists():
        raise SystemExit(f"音声が見つからない: {args.audio}")

    print(
        f"モデル読み込み: {args.model} ({args.compute_type})  ※初回はダウンロードが走る"
    )
    load_started = time.perf_counter()
    model = WhisperModel(
        args.model,
        device="cpu",
        compute_type=args.compute_type,
        cpu_threads=args.threads,
    )
    load_seconds = time.perf_counter() - load_started
    print(f"読み込み完了: {load_seconds:.1f}秒\n")

    started = time.perf_counter()
    segments, info = model.transcribe(
        str(args.audio),
        language="ja",
        beam_size=args.beam_size,
        vad_filter=args.vad,
        initial_prompt=GLOSSARY if args.glossary else None,
    )

    # segments はジェネレータで、回した分だけ推論が進む。
    # 10分近くかかることがあるため、進捗が見えるよう逐次表示する。
    collected: list[dict[str, object]] = []
    for seg in segments:
        elapsed = time.perf_counter() - started
        print(f"[{elapsed:6.1f}s] {seg.start:7.2f}-{seg.end:7.2f}  {seg.text.strip()}")
        collected.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
    total_seconds = time.perf_counter() - started

    text = "".join(str(s["text"]) for s in collected)
    # どの音声・どの設定の結果かをJSON自身に持たせる。
    # 別の音声の結果が同じ表に並ぶ事故が実際に起きたため（compare.py で弾く）
    result = {
        "text": text,
        "segments": collected,
        "language": info.language,
        "meta": {
            "audio": args.audio.name,
            "model": args.model,
            "compute_type": args.compute_type,
            "beam_size": args.beam_size,
            "vad": args.vad,
            "glossary": args.glossary,
            "audio_seconds": info.duration,
            "transcribe_seconds": round(total_seconds, 1),
            "load_seconds": round(load_seconds, 1),
        },
    }

    out_dir = _HERE / "output"
    out_dir.mkdir(exist_ok=True)
    name = (
        args.model
        + ("_vad" if args.vad else "")
        + ("_glossary" if args.glossary else "")
    )
    if args.tag:
        name += f"_{args.tag}"
    out_path = out_dir / f"{name}.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    audio_seconds = info.duration
    print("\n" + "=" * 60)
    print(
        f"モデル          : {args.model} / {args.compute_type} / beam={args.beam_size}"
    )
    print(
        f"VAD / 用語集    : {'あり' if args.vad else 'なし'} / {'あり' if args.glossary else 'なし'}"
    )
    print(f"音声の長さ      : {audio_seconds:.1f}秒")
    print(
        f"文字起こし時間  : {total_seconds:.1f}秒  （実時間の {audio_seconds / total_seconds:.1f}倍速）"
    )
    print(f"モデル読み込み  : {load_seconds:.1f}秒")
    print(f"セグメント数    : {len(collected)}")

    if args.no_reference or not args.reference.exists():
        print("文字誤り率(CER): 正解テキストが無いため未計測")
    else:
        reference = load_reference(args.reference)
        # 台本と音声が対応していないまま比較すると、精度が悪いのか
        # 別の台本を見ているだけなのか区別がつかない。長さで検知する
        ratio = len(normalize(text)) / max(len(normalize(reference)), 1)
        if not 0.7 < ratio < 1.3:
            print(
                f"⚠ 文字数が正解の {ratio:.0%} しかない。"
                f"この音声は {args.reference.name} の台本ではない可能性が高い。"
                "CERは参考にならない"
            )
        cer = char_error_rate(reference, text)
        print(f"文字誤り率(CER): {cer:.1%}  （句読点・全半角・空白は無視）")

    print(f"出力            : {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
