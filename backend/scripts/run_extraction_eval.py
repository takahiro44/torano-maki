"""preview API で Before/After 用 JSON を UTF-8 で取り直す補助。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
INPUTS = {
    "1_price": (
        "先日、田中製作所様との商談で、他社より価格が高いと指摘されました。"
        "すぐに値引きせず、「他社の製品とどの点を比較されていますか？」と聞きました。"
        "保守対応の質を重視していると分かったので、24時間対応と現地エンジニア常駐を説明したところ、"
        "価格差を理解いただき受注できました。"
    ),
    "2_handover": (
        "B商事の担当が来月から交代。前任との関係を新任に引き継がないと、更新の話が止まる。"
    ),
    "3_budget": (
        "A社の初回訪問で、標準プラン360万円が年間予算300万円を超えると言われた。"
        "その場で値引きせず、営業部30名だけの段階導入を出した。"
        "「稟議しやすい」と言われ、次回は情シス入りの見積になった。"
    ),
    "4_support": (
        "専任のIT担当がいない中小のお客様には、設定代行・管理者研修・24時間窓口のプレミアムサポートを出し、"
        "導入後1か月は週次で伴走している。定着せずに解約されることが減った。"
    ),
}


def run(label: str) -> None:
    out: dict = {}
    with httpx.Client(timeout=180.0) as client:
        for key, text in INPUTS.items():
            print(f"{label} {key} ...", flush=True)
            t0 = time.perf_counter()
            res = client.post(
                "http://127.0.0.1:8000/ingest/text/preview",
                json={"raw_text": text},
            )
            res.raise_for_status()
            body = res.json()
            elapsed = round(time.perf_counter() - t0, 1)
            items = []
            for it in body.get("extracted") or []:
                items.append(
                    {
                        "title": it.get("title"),
                        "situation": it.get("situation"),
                        "problem": it.get("problem"),
                        "judgment": it.get("judgment"),
                        "action": it.get("action"),
                        "reasoning": it.get("reasoning"),
                        "outcome": it.get("outcome"),
                        "lesson": it.get("lesson"),
                        "industry": it.get("industry"),
                        "product": it.get("product"),
                        "sales_stage": it.get("sales_stage"),
                    }
                )
            out[key] = {"elapsed_sec": elapsed, "count": len(items), "items": items}
    path = ROOT / "docs" / f"_eval_{label}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}", flush=True)


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "after"
    run(label)
