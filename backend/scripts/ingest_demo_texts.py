"""デモ原文15件を LLM 抽出し、DB に draft で入れる。

使い方（API 起動後）:
    cd backend
    uv run python scripts/ingest_demo_texts.py
    uv run python scripts/ingest_demo_texts.py --confirm   # 続けて confirmed にする
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

DEFAULT_API = "http://127.0.0.1:8000"
TEXTS_FILE = Path(__file__).resolve().parents[1] / "data" / "demo_source_texts.txt"


def load_texts(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    parts = [p.strip() for p in raw.split("\n---\n")]
    # 先頭の説明ブロックと末尾の空を除く
    texts = [p for p in parts if p and not p.startswith("#")]
    return texts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--confirm", action="store_true", help="抽出後に confirmed にする")
    args = parser.parse_args()

    texts = load_texts(TEXTS_FILE)
    if len(texts) != 15:
        print(f"原文が15件ではありません（{len(texts)}件）: {TEXTS_FILE}", file=sys.stderr)
        return 1

    client = httpx.Client(base_url=args.api, timeout=180.0)
    try:
        client.get("/health")
    except httpx.HTTPError:
        print(f"APIに接続できません（{args.api}）", file=sys.stderr)
        return 1

    saved_ids: list[str] = []
    for i, text in enumerate(texts, 1):
        print(f"[{i}/15] 抽出中… {text[:40].replace(chr(10), ' ')}…")
        res = client.post("/ingest/text", json={"raw_text": text})
        if res.status_code not in (200, 201):
            print(f"  失敗: HTTP {res.status_code} {res.text}", file=sys.stderr)
            return 1
        body = res.json()
        rows = body.get("saved") or []
        print(f"  → {len(rows)}件: " + ", ".join(r.get("title", "")[:24] for r in rows))
        for note in body.get("notes") or []:
            print(f"  注記: {note}")
        saved_ids.extend(r["id"] for r in rows)

    if args.confirm:
        for kid in saved_ids:
            patch = client.patch(f"/knowledge/{kid}/status", json={"status": "confirmed"})
            if patch.status_code != 200:
                print(f"承認失敗 {kid}: {patch.status_code} {patch.text}", file=sys.stderr)
                return 1
        print(f"confirmed にしました: {len(saved_ids)}件")

    print(f"\n完了。件数 {client.get('/knowledge/count').json()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
