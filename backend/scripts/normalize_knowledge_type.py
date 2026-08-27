"""既存DBのknowledge_typeをbusiness/casualへ正規化する。

**なぜ `load_extraction_json.py --replace` では不十分か。**
`--replace` は該当商談を一度削除してから入れ直すため、ロープレでその
ナレッジを使ったことがあると `roleplay_session_knowledge` の外部キーに
引っかかって止まる（実測で確認済み）。DELETEを伴わない単純な
UPDATEなら、ロープレ・上司レビュー等の参照があっても安全に直せる。

対象は knowledge_type が "business" / "casual" のどちらでもない行だけ。
2026-08-24時点の検証JSON由来の分類（sales_technique 等）は、
全件が商談から抽出された営業ナレッジのため "business" に寄せる。

使い方:
    cd backend
    uv run python scripts/normalize_knowledge_type.py            # 適用する
    uv run python scripts/normalize_knowledge_type.py --dry-run  # 件数だけ見る
"""

from __future__ import annotations

import argparse

from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.models.tables import KnowledgeUnitTable

_KNOWN_CATEGORIES = ("business", "casual")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="更新せず、対象件数と現在の分類だけを表示する",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(
            select(KnowledgeUnitTable.knowledge_type, func.count())
            .where(KnowledgeUnitTable.knowledge_type.notin_(_KNOWN_CATEGORIES))
            .group_by(KnowledgeUnitTable.knowledge_type)
        ).all()

        if not rows:
            print("対象はありません（すでにbusiness/casualのみです）")
            return 0

        total = sum(count for _, count in rows)
        print(f"business/casual以外: {total}件")
        for value, count in rows:
            print(f"  {value:<24} {count:>4}件")

        if args.dry_run:
            print("\n--dry-run のため更新していません")
            return 0

        db.execute(
            update(KnowledgeUnitTable)
            .where(KnowledgeUnitTable.knowledge_type.notin_(_KNOWN_CATEGORIES))
            .values(knowledge_type="business")
        )
        db.commit()
        print(f"\n{total}件を business へ更新しました")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
