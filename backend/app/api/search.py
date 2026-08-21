"""ナレッジ探索のエンドポイント。担当: CLAUDE.md 1.1 を参照。

検索結果には必ず出典（source_id）を含める（CLAUDE.md 6章）。
"""

from fastapi import APIRouter

router = APIRouter(prefix="/search", tags=["search"])

# TODO: 以下を実装する
#   POST /search  クエリと絞り込み条件を受け取り、出典つきで返す
