"""Before/After 抽出比較レポートを生成する。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
before = json.loads((ROOT / "docs/_eval_before.json").read_text(encoding="utf-8-sig"))
after = json.loads((ROOT / "docs/_eval_after.json").read_text(encoding="utf-8-sig"))

labels = {
    "1_price": "【1】価格反論",
    "2_handover": "【2】担当交代（走り書き）",
    "3_budget": "【3】予算超過",
    "4_support": "【4】IT不在サポート",
}

checks = {
    "1_price": ["田中製作所", "比較", "24時間", "現地", "受注"],
    "2_handover": ["B商事", "交代", "引き継", "更新"],
    "3_budget": ["A社", "360", "300", "30名", "段階導入", "情シス"],
    "4_support": ["IT", "設定代行", "24時間", "週次", "解約"],
}


def markers(text: str | None, keys: list[str]) -> list[str]:
    if not text:
        return []
    return [k for k in keys if k in text]


def blob(item: dict) -> str:
    fields = ("situation", "problem", "action", "outcome", "lesson")
    values = (str(item.get(f) or "") for f in fields)
    return " ".join(values)


lines: list[str] = [
    "抽出プロンプト調整 Before/After 検証報告",
    "日付: 2026-08-26",
    "ブランチ: feat/extraction-prompt-tune",
    "対象: backend/app/services/extraction.py の _SYSTEM_PROMPT / user指示",
    "方法: POST /ingest/text/preview（DB非保存）",
    "入力: backend/data/demo_live_inputs.txt 【1】〜【4】",
    "",
    "========================================================================",
    "0. 結論",
    "========================================================================",
    "",
]

improved = 0
regressed = 0
same = 0
detail_blocks: list[str] = []

for key, label in labels.items():
    b_items = before[key]["items"]
    a_items = after[key]["items"]
    b = b_items[0] if b_items else {}
    a = a_items[0] if a_items else {}
    keys = checks[key]
    bh = markers(blob(b), keys)
    ah = markers(blob(a), keys)
    if len(ah) > len(bh):
        improved += 1
        verdict = "固有語ヒット増"
    elif len(ah) < len(bh):
        regressed += 1
        verdict = "固有語ヒット減（要確認）"
    else:
        same += 1
        verdict = "固有語ヒット同数"

    detail_blocks.extend(
        [
            "",
            f"--- {label} [{verdict}] ---",
            f"Before 件数={before[key]['count']} ({before[key]['elapsed_sec']}s)  固有語: {bh}",
            f"After  件数={after[key]['count']} ({after[key]['elapsed_sec']}s)  固有語: {ah}",
            "",
            "Before title: " + str(b.get("title")),
            "After  title: " + str(a.get("title")),
            "",
            "Before situation: " + str(b.get("situation")),
            "After  situation: " + str(a.get("situation")),
            "",
            "Before action: " + str(b.get("action")),
            "After  action: " + str(a.get("action")),
            "",
            "Before lesson: " + str(b.get("lesson")),
            "After  lesson: " + str(a.get("lesson")),
            "",
            "Before outcome: " + str(b.get("outcome")),
            "After  outcome: " + str(a.get("outcome")),
        ]
    )

hit_summary = f"固有語ヒット: 増 {improved} / 減 {regressed} / 同数 {same}"
case_count = f"（全 {len(labels)} ケース）"
lines.append(hit_summary + case_count)
focus = "重点: lesson が教科書調から具体行動を含む文に寄ったか。"
regression_note = "回帰で【1】の固有情報が落ちていないか。"
lines.append(focus + regression_note)
lines.append("")
lines.append("========================================================================")
lines.append("1. ケース別比較")
lines.append("========================================================================")
lines.extend(detail_blocks)
lines.extend(
    [
        "",
        "========================================================================",
        "2. 人手判定メモ",
        "========================================================================",
        "",
        "【1】価格反論: 田中製作所・比較軸・24時間対応・受注が残っていれば合格（回帰なし）。",
        "【2】走り書き: action が null のままでも可。一般論で埋めない方がよい。",
        "    lesson が「B商事の引き継ぎをしないと更新が止まる」レベルなら改善。",
        "【3】予算: 360万/300万/30名/情シス残存。lesson の「〜を進められる」型が減れば改善。",
        "【4】サポート: 設定代行・週次・解約が残ること。",
        "",
        "生データ: docs/_eval_before.json / docs/_eval_after.json",
        "",
    ]
)

# Append qualitative judgment based on simple heuristics
lines.append("========================================================================")
lines.append("3. 自動ヒューリスティック判定")
lines.append("========================================================================")
lines.append("")
weak_phrases = ("ことが大切", "することが有効", "することが重要", "が望ましい", "という考え方")
for key, label in labels.items():
    a = after[key]["items"][0] if after[key]["items"] else {}
    b = before[key]["items"][0] if before[key]["items"] else {}
    a_lesson = str(a.get("lesson") or "")
    b_lesson = str(b.get("lesson") or "")
    a_weak = [p for p in weak_phrases if p in a_lesson]
    b_weak = [p for p in weak_phrases if p in b_lesson]
    lines.append(f"{label}")
    lines.append(f"  Before 弱い表現: {b_weak or 'なし'}")
    lines.append(f"  After  弱い表現: {a_weak or 'なし'}")
    if b_weak and not a_weak:
        lines.append("  → lesson の教科書調が減った（改善）")
    elif a_weak and not b_weak:
        lines.append("  → lesson の教科書調が増えた（悪化）")
    elif a_weak and b_weak:
        lines.append("  → 弱い表現は残存（部分改善の余地）")
    else:
        lines.append("  → 弱い表現なし（維持または別観点で評価）")
    lines.append("")

out = ROOT / "docs/extraction-prompt-eval-2026-08-26.txt"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {out}")
print(f"improved={improved} regressed={regressed} same={same}")
