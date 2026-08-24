"""検索の質の回帰テスト。

実装計画 §21 の完了条件「10〜20件入れて自然言語検索で関連情報が取得できる」を
機械的に確認し続けるためのもの。埋め込みモデルや検索の実装を変えたときに、
精度が落ちていないかをここで検知する。

15件を投入して10問を検索するため、他のテストより時間がかかる（1分程度）。
"""

import pytest
from fastapi.testclient import TestClient

from app.seed_data import SEARCH_EXPECTATIONS, SEED_KNOWLEDGE


@pytest.fixture(scope="module")
def _seed_note() -> None:
    """このモジュールが重い理由を実行者に伝える。"""
    print(f"\n（{len(SEED_KNOWLEDGE)}件の埋め込みを行うため時間がかかります）")


@pytest.fixture
def seeded(client: TestClient, _seed_note: None) -> TestClient:
    for content in SEED_KNOWLEDGE:
        res = client.post(
            "/knowledge",
            json={"title": content[:100], "situation": content, "status": "confirmed"},
        )
        assert res.status_code == 201, res.text
    return client


def test_期待した項目が1位で返る(seeded: TestClient) -> None:
    """事前に固定した10問すべてで、期待する項目が1位になること。

    どれか1問でも落ちたら、どのクエリが外れたかを一覧で示す。
    1問ずつ parametrize すると毎回15件を投入し直すことになり遅いため、
    まとめて実行している。
    """
    failures: list[str] = []
    for query, expected_phrase in SEARCH_EXPECTATIONS:
        hits = seeded.post("/search", json={"query": query, "top_k": 1}).json()
        top = hits[0]["content"] if hits else "(結果なし)"
        if expected_phrase not in top:
            failures.append(
                f"  「{query}」\n     期待: …{expected_phrase}…\n     実際: {top[:50]}…"
            )

    assert not failures, (
        f"{len(failures)}/{len(SEARCH_EXPECTATIONS)}問で期待した項目が1位になりませんでした:\n"
        + "\n".join(failures)
    )


def test_無関係なクエリでも結果は返る(seeded: TestClient) -> None:
    """しきい値で足切りしない仕様の固定。

    e5のスコアは無関係でも0.78程度出るため、絶対値では判定できない
    （docs/setup-notes.md）。仕様を変える場合はこのテストも一緒に直すこと。
    画面側では「該当が無くても近い順に表示する」と明記している。
    """
    hits = seeded.post("/search", json={"query": "今日の天気", "top_k": 5}).json()
    assert len(hits) == 5


def test_同じ話題で結論が違う項目を状況で使い分けられる(seeded: TestClient) -> None:
    """値引きとサポートに、状況の違う項目を2件ずつ入れてある。

    単なるキーワード一致なら区別できない。意味で引けているかの確認。
    """
    # 「情報システム部がしっかりしている会社」のようなクエリは使わない。
    # 別のナレッジに「情報システム部の佐藤さん」という語が含まれており、
    # 特徴的な語に引きずられてそちらが1位になる（docs/setup-notes.md）。
    # ここでは検索の使い分けを見たいので、語の重なりが無いクエリを選んでいる。
    cases = [
        ("検討初期に高いと言われた", "その場で値引きを提示しない"),
        ("更新時に競合見積を出された", "最大8%"),
        ("IT担当者がいない会社", "プレミアムサポートを提案"),
        ("社内ヘルプデスクがある顧客", "無理に勧めない"),
    ]
    for query, expected in cases:
        top = seeded.post("/search", json={"query": query, "top_k": 1}).json()[0]["content"]
        assert expected in top, f"「{query}」の1位が想定と違う: {top[:50]}"
