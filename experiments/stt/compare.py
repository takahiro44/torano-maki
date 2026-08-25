"""output/ の結果を横並びで比較する。

CERだけを見ていると欠落を見逃す（medium + VAD が14秒分を落としたのに
CERは改善して見えた）。**落ちた文字数**と**業務用語の取得率**を必ず併記する。
"""

from __future__ import annotations

import difflib
import json
import unicodedata
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]

# 連続してこれ以上落ちていたら「発話が丸ごと消えた」とみなす。
# 助詞1つの脱落と、文の消失を区別するための閾値
_DROP_THRESHOLD = 15

# ナレッジとして価値が高く、誤ると気づきにくい語
_KEY_TERMS = ["納期", "代替", "新人", "FAX", "Excel", "SMILE", "2nd"]

_IGNORED = set("、。，．・！？!?「」『』（）()〜~ー-…　 \t\n")


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text) if c not in _IGNORED)


def char_error_rate(ref: str, hyp: str) -> float:
    if not ref:
        return 0.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i]
        for j, h in enumerate(hyp, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def dropped_spans(ref: str, hyp: str) -> list[str]:
    """認識結果から丸ごと欠落した箇所を返す。

    LLMでは復元できない唯一の誤りなので、誤変換とは別に数える。
    """
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    return [
        ref[i1:i2]
        for tag, i1, i2, _, _ in sm.get_opcodes()
        if tag in ("delete", "replace") and i2 - i1 >= _DROP_THRESHOLD
    ]


def main() -> None:
    reference_path = _REPO_ROOT / "tts-demo" / "scripts" / "01_order_entry.json"
    script = json.loads(reference_path.read_text(encoding="utf-8"))
    reference = normalize("".join(t["text"] for t in script["turns"]))

    rows = []
    for path in sorted((_HERE / "output").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        hyp = normalize(data.get("text", ""))
        # 音声が違う結果が混ざっていると比較にならないので、長さで弾く
        if not 0.7 < len(hyp) / len(reference) < 1.3:
            print(f"skip {path.name}（正解と長さが合わない。別の音声の結果）")
            continue
        drops = dropped_spans(reference, hyp)
        rows.append(
            {
                "name": path.stem,
                "cer": char_error_rate(reference, hyp),
                "segments": len(data.get("segments", [])),
                "drops": drops,
                "terms": {t: hyp.count(t) for t in _KEY_TERMS},
            }
        )

    ref_terms = {t: reference.count(t) for t in _KEY_TERMS}

    header = f"{'設定':<22}{'CER':>7}{'欠落':>7}{'seg':>6}  " + "".join(
        f"{t:>8}" for t in _KEY_TERMS
    )
    print("\n" + header)
    print("-" * 100)
    print(
        f"{'（正解）':<21}{'-':>7}{'-':>7}{len(script['turns']):>6}  "
        + "".join(f"{ref_terms[t]:>8}" for t in _KEY_TERMS)
    )
    for r in rows:
        dropped_chars = sum(len(d) for d in r["drops"])
        print(
            f"{r['name']:<22}{r['cer']:>6.1%}{dropped_chars:>6}字{r['segments']:>6}  "
            + "".join(f"{r['terms'][t]:>8}" for t in _KEY_TERMS)
        )

    print("\n=== 欠落した箇所（LLMでは復元できない） ===")
    for r in rows:
        if not r["drops"]:
            print(f"{r['name']}: なし")
            continue
        print(f"{r['name']}:")
        for d in r["drops"]:
            print(f"    「{d[:60]}{'…' if len(d) > 60 else ''}」")


if __name__ == "__main__":
    main()
