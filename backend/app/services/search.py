"""ナレッジ検索。Semantic + Lexical を RRF で統合するハイブリッド検索。

検索結果には必ず出典（source_id）を含める（CLAUDE.md 6章）。
出典がないと、利用者が内容を検証できず信頼できないため。

**スコアをしきい値で足切りしない。**
e5 の類似度は 0.78〜0.88 の狭い範囲に固まり、無関係な項目でも 0.78 程度は出る。
「0.9以上を関連あり」のような絶対値の判定は機能しない。
順位だけが意味を持つため、top_k 件を返して利用者に選ばせる。
（検証結果は docs/setup-notes.md を参照）

## なぜハイブリッドにするか

ベクトル検索だけでは2つの弱点がある（docs/setup-notes.md 2026-08-21 の検証）。

1. 同じ顧客の情報を漏れなく集める再現率が低い。
   「A社」で引くと上位にB社・C社が混入し、A社の項目が上位から漏れる
2. クエリ中の特徴語が、無関係なレコードの同じ語に引き寄せられる

どちらも「語がそのまま一致しているか」を見れば防げる。
そこで語の一致（Lexical）と意味の近さ（Semantic）を別々に順位付けし、
RRF で統合する。

## Lexical Search に PostgreSQL 標準の全文検索を使っていない理由

**これは BM25 ではないし、tsvector / tsquery による全文検索でもない。**
誤解を避けるため明記しておく。

PostgreSQL の default parser は日本語を分かち書きできず、句点までを
丸ごと1トークンにしてしまう。実測（PostgreSQL 17.11）:

    to_tsvector('simple', '基幹システム保守サービスの導入事例')
      → '基幹システム保守サービスの導入事例':1        ← 1トークン

    to_tsvector(...) @@ to_tsquery('simple', '基幹システム')   → false

`ts_rank` で並べる以前に `tsquery` がヒットしない。日本語辞書も
分かち書き器も標準には無いため、標準FTSでは Lexical Search が成立しない。

代わりに `pg_trgm` の `word_similarity()` を使う。これは
**新しい拡張の追加ではない**（`docker/initdb/01_init.sql` で有効化済み、
`idx_knowledge_search_trgm` も既にある）。文字トライグラムの一致度を見るため
分かち書きを必要とせず、日本語でも固有名詞に強い。

将来 PGroonga などで本物の BM25 を入れる余地は残してある。
差し替えるときは `lexical_search()` の中だけを書き換えればよい。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import Knowledge, KnowledgeSearchResult, KnowledgeStatus
from app.models.tables import KnowledgeUnitTable
from app.services.embedding import embed_query


@dataclass(frozen=True)
class KnowledgeFilter:
    """構造化フィールドによる絞り込み。

    industry / product などは意味の近さで探すより SQL で絞る方が正確なため、
    Semantic に押し込まずここで分離しておく（CLAUDE.md 6章の責務分離）。

    **今回は自然文からの条件抽出を実装していない。** 常に None が渡る。
    将来 LLM でクエリから条件を取り出すようになったとき、
    検索側を書き換えずに済むよう受け口だけ用意してある。
    """

    industry: str | None = None
    product: str | None = None
    sales_stage: str | None = None
    knowledge_type: str | None = None


@dataclass(frozen=True)
class ScoredHit:
    """1つの検索方式が出した1件。順位は列挙順で表す。

    Semantic と Lexical でスコアの意味もスケールも違うため、
    このスコアを方式間で足したり比べたりしてはいけない（RRF を使う理由）。
    表示や デバッグのために持ち回るだけ。
    """

    knowledge_id: UUID
    score: float


def _base_query() -> Select[tuple[KnowledgeUnitTable]]:
    """検索対象の共通条件。

    確認済み（confirmed）かつ未削除のものだけを対象にする。
    AIが抽出しただけで人間が確認していない候補（draft）を混ぜると、
    検証されていない情報が検索結果として出てしまうため。
    """
    return select(KnowledgeUnitTable).where(
        KnowledgeUnitTable.deleted_at.is_(None),
        KnowledgeUnitTable.status == KnowledgeStatus.CONFIRMED,
    )


def _apply_filter(
    stmt: Select[tuple[KnowledgeUnitTable]], filters: KnowledgeFilter | None
) -> Select[tuple[KnowledgeUnitTable]]:
    if filters is None:
        return stmt
    for column, value in (
        (KnowledgeUnitTable.industry, filters.industry),
        (KnowledgeUnitTable.product, filters.product),
        (KnowledgeUnitTable.sales_stage, filters.sales_stage),
        (KnowledgeUnitTable.knowledge_type, filters.knowledge_type),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    return stmt


def semantic_search(
    db: Session,
    query: str,
    top_k: int | None = None,
    filters: KnowledgeFilter | None = None,
) -> list[ScoredHit]:
    """意味の近さで検索する。pgvector のコサイン距離。

    質問の埋め込みは都度その場で作って捨てる。保存する価値がないうえ、
    保存すると passage 用のベクトルと混ざる危険があるため。
    """
    settings = get_settings()
    limit = top_k if top_k is not None else settings.semantic_top_k

    query_vector = embed_query(query)
    # <=> はコサイン距離（0が最も近い）。スコアは分かりやすさのため類似度に直す
    distance = KnowledgeUnitTable.embedding.cosine_distance(query_vector)

    stmt = _apply_filter(_base_query(), filters).where(
        # 埋め込み未生成のものは距離を計算できないため除く
        KnowledgeUnitTable.embedding.is_not(None)
    )
    rows = db.execute(
        stmt.add_columns(distance.label("distance")).order_by(distance).limit(limit)
    ).all()

    return [ScoredHit(row.KnowledgeUnitTable.id, 1.0 - row.distance) for row in rows]


def lexical_search(
    db: Session,
    query: str,
    top_k: int | None = None,
    filters: KnowledgeFilter | None = None,
    min_similarity: float | None = None,
) -> list[ScoredHit]:
    """語の一致で検索する。pg_trgm の文字トライグラム。

    **BM25 でも tsvector による全文検索でもない**（理由はモジュール冒頭）。

    `similarity()` ではなく `word_similarity()` を使う。前者は文字列全体の
    長さで正規化するため、短いクエリを長い search_text にぶつけると
    一致していても 0.09 程度にしかならない。後者はクエリに最も近い部分列を
    探すので、長文の一部に含まれる固有名詞を拾える（実測 0.09 → 0.75）。

    min_similarity 未満を捨てるのは、確信が持てないときに黙る方が
    RRF の結果が良くなるため（理由は config.py の定数コメント）。
    """
    settings = get_settings()
    limit = top_k if top_k is not None else settings.lexical_top_k
    threshold = min_similarity if min_similarity is not None else settings.lexical_min_similarity

    similarity = func.word_similarity(query, KnowledgeUnitTable.search_text)

    stmt = _apply_filter(_base_query(), filters).where(
        KnowledgeUnitTable.search_text.is_not(None),
        similarity >= threshold,
    )
    rows = db.execute(
        stmt.add_columns(similarity.label("similarity")).order_by(similarity.desc()).limit(limit)
    ).all()

    return [ScoredHit(row.KnowledgeUnitTable.id, float(row.similarity)) for row in rows]


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[ScoredHit]],
    k: int | None = None,
) -> list[UUID]:
    """複数の検索結果を順位で統合する。

        RRF_score(d) = Σ 1 / (k + rank(d))

    **スコアを直接足さない理由。** Semantic のコサイン類似度は 0.78〜0.88 に
    固まり、Lexical の word_similarity は 0.0〜0.9 に広がる。
    スケールも分布も違うものを足すと、値域の広い方が常に勝つ。
    順位だけを使えばこの問題が消える。

    k は順位差の効き方を決める。小さいと1位が極端に強くなり、
    大きいと順位の差が薄まる。既定は 60（config.py）。

    DBにも埋め込みにも依存しない純粋関数にしてある。
    ここが検索の中で最も壊れると気づきにくい部分なので、
    単体でテストできる形を保つこと。
    """
    fused_k = k if k is not None else get_settings().rrf_k

    scores: dict[UUID, float] = {}
    # 同点のときの順序を安定させるため、最初に現れた順を覚えておく。
    # dict の挿入順に頼ると、方式ごとの実行順で結果が変わってしまう
    first_seen: dict[UUID, int] = {}

    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.knowledge_id] = scores.get(hit.knowledge_id, 0.0) + 1.0 / (fused_k + rank)
            first_seen.setdefault(hit.knowledge_id, len(first_seen))

    return sorted(scores, key=lambda kid: (-scores[kid], first_seen[kid]))


def hybrid_search(
    db: Session,
    query: str,
    top_k: int | None = None,
    filters: KnowledgeFilter | None = None,
) -> list[KnowledgeSearchResult]:
    """Semantic と Lexical を RRF で統合して Knowledge を返す。

    候補の取得（ID と順位）と、本体の取り出しを分けてある。
    将来 Reranker を入れるときは、RRF が返した ID 列を
    `rerank(query, candidates)` に通してから `_load_knowledge()` を呼べばよく、
    この関数以外は触らずに済む。
    """
    settings = get_settings()
    limit = top_k if top_k is not None else settings.hybrid_top_k

    semantic_hits = semantic_search(db, query, filters=filters)
    lexical_hits = lexical_search(db, query, filters=filters)

    fused_ids = reciprocal_rank_fusion([semantic_hits, lexical_hits])

    # ここに rerank(query, fused_ids) が入る（今回は実装しない）
    selected = fused_ids[:limit]

    return _load_knowledge(db, selected, semantic_hits, lexical_hits, settings.rrf_k)


def _load_knowledge(
    db: Session,
    knowledge_ids: Sequence[UUID],
    semantic_hits: Sequence[ScoredHit],
    lexical_hits: Sequence[ScoredHit],
    rrf_k: int,
) -> list[KnowledgeSearchResult]:
    """ID 列を Knowledge 本体に戻す。順位は引数の順を保つ。

    IN 句は順序を保証しないため、取得後に並べ直している。
    """
    if not knowledge_ids:
        return []

    rows = {
        row.id: row
        for row in db.execute(
            select(KnowledgeUnitTable).where(KnowledgeUnitTable.id.in_(knowledge_ids))
        ).scalars()
    }

    semantic_rank = {hit.knowledge_id: i for i, hit in enumerate(semantic_hits, start=1)}
    lexical_rank = {hit.knowledge_id: i for i, hit in enumerate(lexical_hits, start=1)}
    semantic_score = {hit.knowledge_id: hit.score for hit in semantic_hits}
    lexical_score = {hit.knowledge_id: hit.score for hit in lexical_hits}

    results: list[KnowledgeSearchResult] = []
    for kid in knowledge_ids:
        row = rows.get(kid)
        if row is None:  # 検索と取得の間に削除された場合
            continue
        rrf_score = sum(
            1.0 / (rrf_k + rank)
            for rank in (semantic_rank.get(kid), lexical_rank.get(kid))
            if rank is not None
        )
        results.append(
            KnowledgeSearchResult(
                **Knowledge.model_validate(row).model_dump(),
                score=rrf_score,
                semantic_score=semantic_score.get(kid),
                lexical_score=lexical_score.get(kid),
                semantic_rank=semantic_rank.get(kid),
                lexical_rank=lexical_rank.get(kid),
            )
        )
    return results


def search_knowledge(
    db: Session,
    query: str,
    top_k: int,
    filters: KnowledgeFilter | None = None,
) -> list[KnowledgeSearchResult]:
    """自然文でナレッジを検索する。API から呼ばれる入口。"""
    return hybrid_search(db, query, top_k=top_k, filters=filters)
