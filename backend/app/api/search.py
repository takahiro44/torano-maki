"""ナレッジ探索のエンドポイント。

検索結果には必ず出典（source_id）を含める（CLAUDE.md 6章）。
ロジックは services/search.py にある。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.knowledge import KnowledgeSearchResult, SearchRequest
from app.services.search import KnowledgeFilter, search_knowledge

router = APIRouter(prefix="/search", tags=["search"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=list[KnowledgeSearchResult])
def search(payload: SearchRequest, db: DbSession) -> list[KnowledgeSearchResult]:
    """自然文でナレッジを検索する。

    ベクトル検索（意味）と pg_trgm による語彙検索を RRF で統合している。
    リクエスト・レスポンスの形は従来どおりで、`score` の意味だけが
    コサイン類似度から RRF スコアに変わった（内訳は semantic_score /
    lexical_score で参照できる）。

    スコアは参考値。しきい値で足切りせず top_k 件を返す
    （理由は services/search.py のdocstring）。

    `knowledge_type` を指定すると business/casual のどちらかに絞れる。
    省略時は両方を対象にする（フロントの手動検索の既定はこちら）。
    """
    filters = (
        KnowledgeFilter(knowledge_type=payload.knowledge_type)
        if payload.knowledge_type is not None
        else None
    )
    return search_knowledge(db, payload.query, payload.top_k, filters=filters)
