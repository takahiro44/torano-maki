"""検索方式ごとのテストと、3方式の比較評価。

`test_search_quality.py` が「1位が変わっていないか」の回帰テストなのに対し、
こちらは **Semantic / Lexical / Hybrid それぞれの性格**を固定する。
どの方式が何に強いかが崩れたときに気づけるようにしておくのが目的。

RRF そのもののテストは `test_rrf.py`（DB非依存）にある。

シード16件の埋め込みを行うため時間がかかる（1分程度）。
"""

from collections.abc import Callable
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.seed_data import EVAL_CASES, SEED_KNOWLEDGE
from app.services.search import (
    KnowledgeFilter,
    ScoredHit,
    hybrid_search,
    lexical_search,
    semantic_search,
)

# 検索方式の型。比較評価で3方式を同じように扱うために揃えている
SearchFn = Callable[[Session, str], list[ScoredHit]]


@pytest.fixture(scope="module")
def _seed_note() -> None:
    print(f"\n（{len(SEED_KNOWLEDGE)}件の埋め込みを行うため時間がかかります）")


@pytest.fixture
def seeded(client: TestClient, db: Session, _seed_note: None) -> Session:
    """シードを投入したDBセッションを返す。

    サービス層を直接呼ぶテストが多いため、投入は API 経由（search_text と
    embedding の生成をアプリと同じ経路に通したい）、検証はセッション経由にする。
    """
    for content in SEED_KNOWLEDGE:
        res = client.post(
            "/knowledge",
            json={"title": content[:100], "situation": content, "status": "confirmed"},
        )
        assert res.status_code == 201, res.text
    return db


def _contents(db: Session, ids: list[UUID]) -> list[str]:
    """ID列を search_text に戻す。どの項目が返ったかを語で確認するため。"""
    from sqlalchemy import select

    from app.models.tables import KnowledgeUnitTable

    rows = {
        r.id: (r.search_text or "")
        for r in db.execute(
            select(KnowledgeUnitTable).where(KnowledgeUnitTable.id.in_(ids))
        ).scalars()
    }
    return [rows.get(i, "") for i in ids]


# --- Semantic Search ---------------------------------------------------------


def test_semantic_言い換えでも関連ナレッジを取得できる(seeded: Session) -> None:
    """語が重ならないクエリで引けること。Semantic を入れている理由そのもの。

    「値段が高い」という語はナレッジ本文に無く、本文側は
    「価格が高い」「初期費用を抑え」と書かれている。
    語彙検索では引けず、意味の近さでしか辿り着けない。
    """
    hits = semantic_search(seeded, "値段が高いと言われた", top_k=3)
    top = _contents(seeded, [h.knowledge_id for h in hits])

    assert any("その場で値引きを提示しない" in t for t in top), top[0][:60]


def test_semantic_スコアは類似度で降順(seeded: Session) -> None:
    """順位とスコアの向きが一致していること。

    コサイン「距離」から「類似度」に変換しているため、
    符号を取り違えると順位が逆転する。エラーにはならないので固定しておく。
    """
    hits = semantic_search(seeded, "初回訪問で気をつけること", top_k=5)

    assert len(hits) == 5
    assert hits == sorted(hits, key=lambda h: -h.score)
    assert all(0.0 <= h.score <= 1.0 for h in hits)


# --- Lexical Search ----------------------------------------------------------


def test_lexical_商品名を含むナレッジが上位に来る(seeded: Session) -> None:
    """固有語での検索。Semantic が苦手な領域を埋めるのが Lexical の役目。"""
    hits = lexical_search(seeded, "基幹システム", top_k=5)
    top = _contents(seeded, [h.knowledge_id for h in hits])

    assert top, "「基幹システム」で語彙検索が1件も返らなかった"
    assert "基幹システム保守サービス" in top[0], top[0][:60]


def test_lexical_固有名詞を1位で引ける(seeded: Session) -> None:
    """製品名・社名。表記がそのまま一致するので語彙検索が最も確実。"""
    for query, expected in [
        ("CloudLedger", "CloudLedger"),
        ("NimbusCRM", "NimbusCRM"),
        ("東都物流", "東都物流"),
    ]:
        hits = lexical_search(seeded, query, top_k=3)
        top = _contents(seeded, [h.knowledge_id for h in hits])
        assert top, f"「{query}」で語彙検索が1件も返らなかった"
        assert expected in top[0], f"「{query}」の1位が想定と違う: {top[0][:60]}"


def test_lexical_無関係なクエリでは何も返さない(seeded: Session) -> None:
    """**Semantic と決定的に違う性質。**

    e5 は無関係でも 0.78 程度出るため足切りできないが、
    trgm は無関係なら 0.0 になる。確信が持てないときに黙ることで、
    RRF に順位のノイズを流し込まずに済む。ここが効かなくなると
    Hybrid の結果が静かに悪化するため固定しておく。
    """
    assert lexical_search(seeded, "今日の天気", top_k=5) == []


def test_lexical_閾値を下げると結果が増える(seeded: Session) -> None:
    """足切りが実際に効いていることの確認。

    閾値が無視されていても通常のテストは通ってしまうため、
    ここだけは明示的に比較する。
    """
    strict = lexical_search(seeded, "更新時の競合見積と値引き条件", min_similarity=0.4)
    loose = lexical_search(seeded, "更新時の競合見積と値引き条件", min_similarity=0.0)

    assert len(strict) < len(loose)


def test_lexical_クエリの一部しか一致しないときは黙る(seeded: Session) -> None:
    """**実際に回帰を起こしたケースの固定。**

    「A社の導入予定」は『A社の』の部分だけがどのA社ナレッジにも一致するため、
    word_similarity が 0.375 対 0.25 の僅差になり、どれが1位になるかはほぼ運。
    これを RRF に流したところ、semantic が正しく出していた1位
    （10月に大阪支社で試験導入）が別のA社ナレッジに押し出された。

    「一部が一致しただけ」と「クエリ全体が一致した」の境目が閾値の役割。
    ここが緩むと、語彙検索が自信のない順位で semantic を上書きし始める。
    """
    assert lexical_search(seeded, "A社の導入予定") == []

    # 一方、語として完結している「A社」なら4件すべてを拾う（再現率の担保）
    assert len(lexical_search(seeded, "A社")) >= 4


def test_lexical_スコアは降順(seeded: Session) -> None:
    hits = lexical_search(seeded, "A社", top_k=10)

    assert hits == sorted(hits, key=lambda h: -h.score)


# --- Hybrid Search -----------------------------------------------------------


def test_hybrid_semantic側だけで上位の項目を取得できる(seeded: Session) -> None:
    """語が一致しない言い換えクエリ。Lexical は沈黙し、Semantic が効く。"""
    results = hybrid_search(seeded, "値段が高いと言われた", top_k=5)

    assert any("その場で値引きを提示しない" in r.content for r in results)


def test_hybrid_lexical側だけで上位の項目を取得できる(seeded: Session) -> None:
    """固有語クエリ。Semantic だけだと埋もれる項目が拾えること。"""
    results = hybrid_search(seeded, "基幹システム", top_k=5)

    assert any("基幹システム保守サービス" in r.content for r in results)


def test_hybrid_両方の結果が統合される(seeded: Session) -> None:
    """統合の実質を確認する。

    片方の方式だけが拾った項目が、両方とも結果に残っていること。
    どちらか一方に倒れていたら統合できていない。
    """
    query = "A社"
    sem_ids = {h.knowledge_id for h in semantic_search(seeded, query)}
    lex_ids = {h.knowledge_id for h in lexical_search(seeded, query)}
    assert lex_ids, "この検証には語彙検索が結果を返す必要がある"

    results = hybrid_search(seeded, query, top_k=5)
    result_ids = {r.id for r in results}

    assert result_ids & sem_ids, "ベクトル検索の結果が統合結果に残っていない"
    assert result_ids & lex_ids, "語彙検索の結果が統合結果に残っていない"


def test_hybrid_順位の内訳を返す(seeded: Session) -> None:
    """なぜその順位になったかを追えること。

    RRF スコアだけだと調整のしようがない。どちらの方式が効いたのかを
    見られるようにしてある。
    """
    results = hybrid_search(seeded, "CloudLedger", top_k=5)

    assert results
    top = results[0]
    assert top.score > 0
    # 少なくとも片方の方式には拾われているはず
    assert top.semantic_rank is not None or top.lexical_rank is not None
    if top.semantic_rank is not None:
        assert top.semantic_score is not None
    if top.lexical_rank is not None:
        assert top.lexical_score is not None


def test_hybrid_出典を辿れる(seeded: Session) -> None:
    """CLAUDE.md 6章。検索結果から元のナレッジへ辿れる状態を壊さないこと。"""
    results = hybrid_search(seeded, "初回訪問で気をつけること", top_k=3)

    assert results
    for r in results:
        assert r.id is not None
        assert r.title
        assert r.content
        # source_id は data_source_id と同じ。手入力ナレッジでは None になりうるが、
        # フィールド自体が消えていないことを確認する
        assert "source_id" in r.model_dump()


def test_hybrid_確認済みのナレッジだけを返す(seeded: Session, client: TestClient) -> None:
    """draft を混ぜない。人間が検証していない情報を検索結果に出さないため。"""
    res = client.post(
        "/knowledge",
        json={
            "title": "ZzzUnconfirmedWidget の取り扱い",
            "situation": "ZzzUnconfirmedWidget は未確認のナレッジである",
            "status": "draft",
        },
    )
    assert res.status_code == 201, res.text

    results = hybrid_search(seeded, "ZzzUnconfirmedWidget", top_k=10)

    assert not any("ZzzUnconfirmedWidget" in r.content for r in results)


def test_hybrid_構造化フィルタで絞り込める(seeded: Session, client: TestClient) -> None:
    """将来の Structured Filter の受け口が実際に動くこと。

    今回は自然文からの条件抽出を実装していないため API からは使われないが、
    使えない受け口を用意しても意味がないので動作だけ固定しておく。
    """
    res = client.post(
        "/knowledge",
        json={
            "title": "製造業向けの段取り替え支援",
            "situation": "段取り替えの時間短縮を求める顧客に稼働率の実績値を示す",
            "industry": "製造業",
            "status": "confirmed",
        },
    )
    assert res.status_code == 201, res.text

    filtered = hybrid_search(
        seeded, "顧客への提案", top_k=10, filters=KnowledgeFilter(industry="製造業")
    )

    assert filtered, "フィルタ付きで1件も返らなかった"
    assert all(r.industry == "製造業" for r in filtered)


def test_hybrid_top_kを超える件数は返さない(seeded: Session) -> None:
    for k in (1, 3, 5):
        assert len(hybrid_search(seeded, "顧客への提案", top_k=k)) <= k


# --- 比較評価 -----------------------------------------------------------------


def _recall_at_k(found: list[str], expected_phrases: list[str], k: int) -> float:
    """正解のうち上位K件に入ったものの割合。"""
    top = found[:k]
    hit = sum(1 for phrase in expected_phrases if any(phrase in t for t in top))
    return hit / len(expected_phrases)


def test_比較評価_hybridがsemantic単独を下回らない(seeded: Session) -> None:
    """**このテストが今回の変更の存在理由。**

    Semantic / Lexical / Hybrid の Recall@5 を EVAL_CASES で比較し、
    Hybrid が Semantic 単独より悪くなっていないことを確認する。

    ハイブリッド化は「苦手を補う」ための変更なので、平均が上がるだけでなく
    **個別のクエリで劣化していない**ことが重要。語彙検索のノイズが
    紛れ込むと、Semantic で取れていたものが押し出される形で静かに悪化する。
    """
    k = 5
    rows: list[tuple[str, float, float, float]] = []

    for query, expected in EVAL_CASES:
        sem = _contents(seeded, [h.knowledge_id for h in semantic_search(seeded, query)])
        lex = _contents(seeded, [h.knowledge_id for h in lexical_search(seeded, query)])
        hyb = [r.content for r in hybrid_search(seeded, query, top_k=k)]
        rows.append(
            (
                query,
                _recall_at_k(sem, expected, k),
                _recall_at_k(lex, expected, k),
                _recall_at_k(hyb, expected, k),
            )
        )

    header = f"\n{'クエリ':<22} {'Semantic':>9} {'Lexical':>8} {'Hybrid':>7}"
    lines = [f"{q:<22} {s:>9.2f} {ll:>8.2f} {h:>7.2f}" for q, s, ll, h in rows]
    n = len(rows)
    avg = (
        f"{'--- 平均 ---':<22} "
        f"{sum(r[1] for r in rows) / n:>9.2f} "
        f"{sum(r[2] for r in rows) / n:>8.2f} "
        f"{sum(r[3] for r in rows) / n:>7.2f}"
    )
    print("\n".join([header, *lines, avg]))

    regressions = [f"  「{q}」 semantic={s:.2f} → hybrid={h:.2f}" for q, s, _, h in rows if h < s]
    assert not regressions, "Hybrid が Semantic 単独より悪化したクエリがあります:\n" + "\n".join(
        regressions
    )

    assert sum(r[3] for r in rows) / n >= sum(r[1] for r in rows) / n


def test_比較評価_同一顧客の再現率がhybridで改善する(seeded: Session) -> None:
    """docs/setup-notes.md 2026-08-21 で記録された弱点の回帰テスト。

    「A社」で引くと上位5件にB社・C社が混入し、A社の項目が漏れていた。
    語彙検索を足すことで4件すべて拾えるようになったことを固定する。
    ここが落ちたら、ハイブリッド化の主目的が失われている。
    """
    query, expected = next((q, e) for q, e in EVAL_CASES if q == "A社")

    sem = _contents(seeded, [h.knowledge_id for h in semantic_search(seeded, query)])
    hyb = [r.content for r in hybrid_search(seeded, query, top_k=5)]

    assert _recall_at_k(hyb, expected, 5) == 1.0, (
        f"A社の項目4件を上位5件で拾えていない（semantic単独では"
        f"{_recall_at_k(sem, expected, 5):.2f}）"
    )
