"""RRF（Reciprocal Rank Fusion）の単体テスト。

**DBにも埋め込みにも依存させない。** RRF は検索の中で最も
「壊れても気づきにくい」部分で、順位がわずかにずれても
検索結果が悪くなるだけで例外は出ない。DBを立てずに一瞬で回せる形にして、
おかしくなったら即座に分かるようにしておく。
"""

from uuid import UUID, uuid4

from app.services.search import ScoredHit, reciprocal_rank_fusion


def _hits(*ids: UUID) -> list[ScoredHit]:
    """順位は列挙順で表す。スコアはRRFに使われないのでダミーでよい。"""
    return [ScoredHit(knowledge_id=i, score=0.0) for i in ids]


def test_両方の検索で上位に出た項目が統合結果でも上位になる() -> None:
    """RRFの目的そのもの。片方だけで1位より、両方で上位の方を優先する。"""
    a, b, c, d, e = (uuid4() for _ in range(5))

    # a は 1位 と 3位、c は 3位 と 1位。b は片方のみ1位ではなく2位止まり
    semantic = _hits(a, b, c)
    lexical = _hits(c, d, a)

    fused = reciprocal_rank_fusion([semantic, lexical], k=60)

    # a と c だけが両方に出ているので、この2件が先頭に来る
    assert set(fused[:2]) == {a, c}
    assert set(fused[2:]) == {b, d}
    assert e not in fused


def test_片方にしか出ない項目も落とさない() -> None:
    """再現率のための性質。統合は絞り込みではない。"""
    a, b, c, d = (uuid4() for _ in range(4))

    fused = reciprocal_rank_fusion([_hits(a, b), _hits(c, d)], k=60)

    assert set(fused) == {a, b, c, d}


def test_順位が高いほどスコアが大きい() -> None:
    """単一の検索結果を通しても順位が保たれること。"""
    ids = [uuid4() for _ in range(4)]

    assert reciprocal_rank_fusion([_hits(*ids)], k=60) == ids


def test_空の検索結果を混ぜても壊れない() -> None:
    """語彙検索は閾値未満だと何も返さない。これが通常運転であること。"""
    a, b = uuid4(), uuid4()

    assert reciprocal_rank_fusion([_hits(a, b), []], k=60) == [a, b]
    assert reciprocal_rank_fusion([[], []], k=60) == []
    assert reciprocal_rank_fusion([], k=60) == []


def test_同点のときの順序が実行のたびに変わらない() -> None:
    """同点は普通に起きる（両方で同じ順位のとき）。

    ここが不安定だと、同じクエリで結果の並びが変わる。
    原因の分からないバグとして現れるため、決定的であることを固定する。
    """
    a, b = uuid4(), uuid4()

    # a と b は互いに逆順の1位・2位。RRFスコアは完全に同じになる
    first = reciprocal_rank_fusion([_hits(a, b), _hits(b, a)], k=60)
    for _ in range(5):
        assert reciprocal_rank_fusion([_hits(a, b), _hits(b, a)], k=60) == first

    # 同点なら「先に見つかった方」が前に来る
    assert first == [a, b]


def test_kが小さいほど1位の重みが強くなる() -> None:
    """k の意味を固定する。値を変えるときにこのテストで挙動を確認できる。

    片方の検索で1位の a と、もう片方で1位・2位の b・c を競わせる。
    k が小さいと「1位であること」の価値が上がり、a が勝つ。
    """
    a, b, c = (uuid4() for _ in range(3))
    rankings = [_hits(a), _hits(b, c, a)]

    # k=1 では a のスコア 1/2 + 1/4 = 0.75、b は 1/2 = 0.5
    assert reciprocal_rank_fusion(rankings, k=1)[0] == a

    # k を大きくすると順位差が薄まり、a のスコアは b に近づく。
    # それでも a は2つの検索に出ている分だけ有利であり続ける
    assert reciprocal_rank_fusion(rankings, k=1000)[0] == a


def test_3つ以上の検索結果も統合できる() -> None:
    """将来Rerankerや別の検索方式を足したときに備えた性質。"""
    a, b, c = (uuid4() for _ in range(3))

    fused = reciprocal_rank_fusion([_hits(a, b), _hits(b, c), _hits(b, a)], k=60)

    # b は3つすべてに出ているので1位
    assert fused[0] == b
