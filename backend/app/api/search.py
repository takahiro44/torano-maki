"""ナレッジ探索のエンドポイント。

検索結果には必ず出典（source_id）を含める（CLAUDE.md 6章）。
ロジックは services/search.py にある。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.knowledge import KnowledgeSearchResult, SearchRequest
from app.services.search import search_knowledge

router = APIRouter(prefix="/search", tags=["search"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=list[KnowledgeSearchResult])
def search(payload: SearchRequest, db: DbSession) -> list[KnowledgeSearchResult]:
    """自然文でナレッジを検索する。

    スコアは参考値。しきい値で足切りせず top_k 件を返す
    （理由は services/search.py のdocstring）。
    """
    return search_knowledge(db, payload.query, payload.top_k)
