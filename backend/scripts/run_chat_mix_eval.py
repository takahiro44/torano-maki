"""チャット混在防止の Before/After 採取。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = [
    {
        "id": "price_generic",
        "q": "価格が高いと言われたとき、どう対応した事例はありますか？",
        "note": "複数案件がヒットしやすい。会社名を混ぜていないか見る",
    },
    {
        "id": "a_company",
        "q": "A社への提案で注意すべきことは？",
        "note": "A社以外の会社の事実を混ぜていないか見る",
    },
]


def run(label: str) -> None:
    out: dict = {"label": label, "cases": []}
    with httpx.Client(timeout=300.0) as client:
        for case in QUESTIONS:
            print(f"{label} {case['id']} ...", flush=True)
            t0 = time.perf_counter()
            res = client.post(
                "http://127.0.0.1:8000/chat",
                json={
                    "messages": [{"role": "user", "content": case["q"]}],
                    "top_k": 5,
                },
            )
            elapsed = round(time.perf_counter() - t0, 1)
            if res.status_code != 200:
                out["cases"].append(
                    {
                        "id": case["id"],
                        "question": case["q"],
                        "note": case["note"],
                        "ok": False,
                        "elapsed_sec": elapsed,
                        "status": res.status_code,
                        "error": res.text[:500],
                    }
                )
                continue
            body = res.json()
            citations = [
                {"title": c.get("title"), "knowledge_id": c.get("knowledge_id")}
                for c in (body.get("citations") or [])
            ]
            tools = [
                {"tool": t.get("tool"), "ok": t.get("ok"), "summary": t.get("summary")}
                for t in (body.get("tool_trace") or [])
            ]
            out["cases"].append(
                {
                    "id": case["id"],
                    "question": case["q"],
                    "note": case["note"],
                    "ok": True,
                    "elapsed_sec": elapsed,
                    "answer": body.get("answer"),
                    "citations": citations,
                    "tool_trace": tools,
                    "usage": body.get("usage"),
                }
            )
    path = ROOT / "docs" / f"_chat_eval_{label}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}", flush=True)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "before")
