"""APIの振る舞いの検証。

各テストはトランザクションでロールバックされるため、
開発中のデータは残らない（conftest.py 参照）。
"""

from fastapi.testclient import TestClient


def _create(client: TestClient, content: str) -> dict:
    res = client.post("/knowledge", json={"content": content})
    assert res.status_code == 201, res.text
    return res.json()


class TestCreate:
    def test_本文だけで登録できる(self, client: TestClient) -> None:
        row = _create(client, "A社はサポート体制を重視する")
        assert row["status"] == "confirmed"
        assert row["source_type"] == "manual"

    def test_空白だけの本文は422(self, client: TestClient) -> None:
        assert client.post("/knowledge", json={"content": "   "}).status_code == 422

    def test_不正なstatusは422(self, client: TestClient) -> None:
        res = client.post("/knowledge", json={"content": "x", "status": "bogus"})
        assert res.status_code == 422


class TestUpdate:
    def test_明示的なnullは422になる(self, client: TestClient) -> None:
        """500 になっていた不具合の回帰テスト。"""
        row = _create(client, "元の本文")
        for payload in ({"content": None}, {"status": None}):
            res = client.patch(f"/knowledge/{row['id']}", json=payload)
            assert res.status_code == 422, f"{payload} が {res.status_code} になった"
        # 弾かれた場合、行は変わっていないこと
        assert client.get(f"/knowledge/{row['id']}").json()["content"] == "元の本文"

    def test_statusだけ更新できる(self, client: TestClient) -> None:
        row = _create(client, "本文")
        res = client.patch(f"/knowledge/{row['id']}", json={"status": "draft"})
        assert res.status_code == 200
        assert res.json()["status"] == "draft"
        assert res.json()["content"] == "本文"

    def test_存在しないIDは404(self, client: TestClient) -> None:
        missing = "00000000-0000-0000-0000-000000000000"
        assert client.patch(f"/knowledge/{missing}", json={"content": "x"}).status_code == 404


class TestDelete:
    def test_論理削除後は取得できない(self, client: TestClient) -> None:
        row = _create(client, "消す対象")
        assert client.delete(f"/knowledge/{row['id']}").status_code == 204
        assert client.get(f"/knowledge/{row['id']}").status_code == 404

    def test_削除したものは一覧に出ない(self, client: TestClient) -> None:
        row = _create(client, "一覧から消える対象")
        client.delete(f"/knowledge/{row['id']}")
        ids = [r["id"] for r in client.get("/knowledge").json()]
        assert row["id"] not in ids


class TestSearch:
    def test_登録した内容が検索で見つかる(self, client: TestClient) -> None:
        _create(client, "値引き交渉では運用コスト削減額を先に示す")
        hits = client.post("/search", json={"query": "価格交渉のやり方", "top_k": 5}).json()
        assert any("運用コスト削減額" in h["content"] for h in hits)

    def test_検索結果に出典が含まれる(self, client: TestClient) -> None:
        """CLAUDE.md 6章。出典が無いと利用者が内容を検証できない。"""
        _create(client, "出典つきで返ること")
        hit = client.post("/search", json={"query": "出典", "top_k": 1}).json()[0]
        assert "source_type" in hit and "source_id" in hit and "score" in hit

    def test_draftは検索対象外(self, client: TestClient) -> None:
        """人間が確認していない候補が検索結果に出てはいけない。"""
        row = _create(client, "確認前の候補データ")
        client.patch(f"/knowledge/{row['id']}", json={"status": "draft"})
        hits = client.post("/search", json={"query": "確認前の候補", "top_k": 10}).json()
        assert all(h["id"] != row["id"] for h in hits)

    def test_論理削除したものは検索対象外(self, client: TestClient) -> None:
        row = _create(client, "削除済みのナレッジ")
        client.delete(f"/knowledge/{row['id']}")
        hits = client.post("/search", json={"query": "削除済み", "top_k": 10}).json()
        assert all(h["id"] != row["id"] for h in hits)

    def test_本文を更新すると検索結果に反映される(self, client: TestClient) -> None:
        """再埋め込みが行われているかの確認。

        本文とベクトルがズレると、検索で別の内容がヒットするようになる。
        """
        row = _create(client, "官公庁案件は法務審査に4週間かかる")
        client.patch(f"/knowledge/{row['id']}", json={"content": "犬の散歩は朝が気持ちいい"})
        hits = client.post("/search", json={"query": "犬の散歩", "top_k": 1}).json()
        assert hits[0]["id"] == row["id"]
        assert hits[0]["content"] == "犬の散歩は朝が気持ちいい"

    def test_top_kが0以下は422(self, client: TestClient) -> None:
        assert client.post("/search", json={"query": "a", "top_k": 0}).status_code == 422
