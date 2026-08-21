"""ナレッジ検索。

検索結果には必ず出典（source_id）を含める（CLAUDE.md 6章）。
出典がないと、利用者が内容を検証できず信頼できないため。

**スコアをしきい値で足切りしない。**
e5 の類似度は 0.78〜0.88 の狭い範囲に固まり、無関係な項目でも 0.78 程度は出る。
「0.9以上を関連あり」のような絶対値の判定は機能しない。
順位だけが意味を持つため、top_k 件を返して利用者に選ばせる。
（検証結果は docs/setup-notes.md を参照）
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge import Knowledge, KnowledgeSearchResult, KnowledgeStatus
from app.models.tables import KnowledgeTable
from app.services.embedding import embed_query


def search_knowledge(db: Session, query: str, top_k: int) -> list[KnowledgeSearchResult]:
    """自然文でナレッジを検索する。

    確認済み（confirmed）かつ未削除のものだけを対象にする。
    AIが抽出しただけで人間が確認していない候補（draft）を混ぜると、
    検証されていない情報が検索結果として出てしまうため。
    """
    query_vector = embed_query(query)

    # <=> はコサイン距離（0が最も近い）。スコアは分かりやすさのため類似度に直す
    distance = KnowledgeTable.embedding.cosine_distance(query_vector)

    rows = db.execute(
        select(KnowledgeTable, distance.label("distance"))
        .where(
            KnowledgeTable.deleted_at.is_(None),
            KnowledgeTable.status == KnowledgeStatus.CONFIRMED,
            # 埋め込み未生成のものは距離を計算できないため除く
            KnowledgeTable.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(top_k)
    ).all()

    return [
        KnowledgeSearchResult(
            **Knowledge.model_validate(row.KnowledgeTable).model_dump(),
            score=1.0 - row.distance,
        )
        for row in rows
    ]
