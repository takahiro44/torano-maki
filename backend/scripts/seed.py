"""サンプルナレッジを投入する。

検索の動作確認やデモの準備を、全員が同じデータで素早く行えるようにするため。
API経由で投入するので、埋め込み生成も本番と同じ経路を通る。

使い方:
    cd backend
    uv run python scripts/seed.py            # 追加投入
    uv run python scripts/seed.py --reset    # 既存を全消ししてから投入

--reset は既存のナレッジを**論理削除**する（APIの DELETE を呼ぶため）。
行自体はDBに残り `deleted_at` が入るだけなので、誤って実行しても
SQLで `deleted_at` を NULL に戻せば復活できる。
指定しない限り消さないのは、作業中のデータを事故で失わないようにするため。
"""

import argparse
import sys

import httpx

from app.seed_data import SEED_KNOWLEDGE

DEFAULT_API = "http://127.0.0.1:8000"


def main() -> int:
    parser = argparse.ArgumentParser(description="サンプルナレッジを投入する")
    parser.add_argument("--api", default=DEFAULT_API, help=f"APIのベースURL（既定: {DEFAULT_API}）")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="投入前に既存のナレッジを論理削除する（行は残るのでSQLで復活可能）",
    )
    args = parser.parse_args()

    client = httpx.Client(base_url=args.api, timeout=120)

    try:
        client.get("/health")
    except httpx.HTTPError:
        print(
            f"APIに接続できません（{args.api}）。\n"
            "  cd backend && uv run uvicorn app.main:app --reload\n"
            "でバックエンドを起動してから実行してください。",
            file=sys.stderr,
        )
        return 1

    if args.reset:
        # API に全削除は用意していない（誤操作の被害が大きいため）ので、
        # 既存分を1件ずつ論理削除する
        existing = client.get("/knowledge", params={"limit": 200}).json()
        for row in existing:
            client.delete(f"/knowledge/{row['id']}")
        print(f"既存 {len(existing)} 件を論理削除しました（行はDBに残ります）")

    for i, content in enumerate(SEED_KNOWLEDGE, 1):
        res = client.post("/knowledge", json={"content": content})
        if res.status_code != 201:
            print(f"{i}件目の投入に失敗: HTTP {res.status_code} {res.text}", file=sys.stderr)
            return 1
        print(f"  [{i}/{len(SEED_KNOWLEDGE)}] {content[:34]}…")

    print(f"\n投入しました: {client.get('/knowledge/count').json()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
