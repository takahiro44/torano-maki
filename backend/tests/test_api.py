"""APIの振る舞いの検証。"""

from fastapi.testclient import TestClient


def _create(client: TestClient, content: str) -> dict:
    res = client.post(
        "/knowledge",
        json={"title": content[:100], "situation": content, "status": "confirmed"},
    )
    assert res.status_code == 201, res.text
    return res.json()


class TestCreate:
    def test_titleだけで登録できる(self, client: TestClient) -> None:
        row = _create(client, "A社はサポート体制を重視する")
        assert row["status"] == "confirmed"
        assert row["source_type"] == "manual"
        assert row["title"] == "A社はサポート体制を重視する"

    def test_空白だけのtitleは422(self, client: TestClient) -> None:
        assert client.post("/knowledge", json={"title": "   "}).status_code == 422

    def test_不正なstatusは422(self, client: TestClient) -> None:
        res = client.post("/knowledge", json={"title": "x", "status": "bogus"})
        assert res.status_code == 422


class TestUpdate:
    def test_明示的なnullは422になる(self, client: TestClient) -> None:
        row = _create(client, "元の本文")
        for payload in ({"title": None}, {"status": None}):
            res = client.patch(f"/knowledge/{row['id']}", json=payload)
            assert res.status_code == 422, f"{payload} が {res.status_code} になった"
        assert client.get(f"/knowledge/{row['id']}").json()["situation"] == "元の本文"

    def test_statusだけ更新できる(self, client: TestClient) -> None:
        row = _create(client, "本文")
        res = client.patch(f"/knowledge/{row['id']}", json={"status": "draft"})
        assert res.status_code == 200
        assert res.json()["status"] == "draft"
        assert res.json()["situation"] == "本文"

    def test_編集画面が送る形で保存できる(self, client: TestClient) -> None:
        """画面（KnowledgeEditor）は変わった項目だけを送り、承認と同時に保存する。

        **消した項目は空文字で送る。** 明示的な null は拒まれるため
        （変更しない項目との区別が付かない）、画面側で空文字に寄せている。
        その約束がここで崩れると、承認が丸ごと422になる。
        """
        row = _create(client, "元の本文")
        client.patch(f"/knowledge/{row['id']}", json={"status": "draft", "lesson": "消される学び"})

        res = client.patch(
            f"/knowledge/{row['id']}",
            json={
                "title": "直したタイトル",
                "situation": "直した状況",
                "lesson": "",
                "status": "confirmed",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "confirmed"
        assert body["title"] == "直したタイトル"
        assert body["situation"] == "直した状況"
        assert not body["lesson"]

    def test_本文を直すと検索の対象も入れ替わる(self, client: TestClient) -> None:
        """編集で search_text と embedding を作り直さないと、直す前の文で当たり続ける。

        **順位ではなく、当たった中身で見る。** 開発中のDBには他のナレッジが
        入っており、1位に来るとは限らない（来ないこと自体は不具合ではない）。
        """
        row = _create(client, "在庫の締め日は毎月20日")
        client.patch(
            f"/knowledge/{row['id']}",
            json={"title": "請求の締め日", "situation": "請求の締め日は毎月末"},
        )

        hits = client.post("/search", json={"query": "請求の締め日は毎月末", "top_k": 50}).json()
        found = next((h for h in hits if h["id"] == row["id"]), None)
        assert found is not None, "直した本文で検索しても、そのナレッジが出てこない"
        assert "毎月末" in found["content"]
        assert "毎月20日" not in found["content"]

    def test_status専用エンドポイント(self, client: TestClient) -> None:
        row = _create(client, "承認する")
        res = client.patch(f"/knowledge/{row['id']}/status", json={"status": "rejected"})
        assert res.status_code == 200
        assert res.json()["status"] == "rejected"

    def test_存在しないIDは404(self, client: TestClient) -> None:
        missing = "00000000-0000-0000-0000-000000000000"
        assert client.patch(f"/knowledge/{missing}", json={"title": "x"}).status_code == 404


class TestListSort:
    def test_タイトル昇順で並べ替えられる(self, client: TestClient) -> None:
        zeta = _create(client, "zzz-sort-probe")
        alpha = _create(client, "aaa-sort-probe")
        rows = client.get(
            "/knowledge", params={"sort": "title", "order": "asc", "limit": 200}
        ).json()
        ids = [r["id"] for r in rows]
        assert ids.index(alpha["id"]) < ids.index(zeta["id"])

    def test_不正なsortは422(self, client: TestClient) -> None:
        assert client.get("/knowledge", params={"sort": "embedding"}).status_code == 422


class TestListFilter:
    def test_業界で絞り込める(self, client: TestClient) -> None:
        mfg = client.post(
            "/knowledge",
            json={"title": "製造向け", "industry": "製造業", "status": "confirmed"},
        ).json()
        client.post(
            "/knowledge",
            json={"title": "金融向け", "industry": "金融", "status": "confirmed"},
        )
        rows = client.get("/knowledge", params={"industry": "製造業", "limit": 200}).json()
        ids = [r["id"] for r in rows]
        assert mfg["id"] in ids
        assert all(r["industry"] == "製造業" for r in rows)


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
        _create(client, "出典つきで返ること")
        hit = client.post("/search", json={"query": "出典", "top_k": 1}).json()[0]
        assert "source_type" in hit and "source_id" in hit and "score" in hit

    def test_draftは検索対象外(self, client: TestClient) -> None:
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
        row = _create(client, "官公庁案件は法務審査に4週間かかる")
        client.patch(f"/knowledge/{row['id']}", json={"situation": "犬の散歩は朝が気持ちいい"})
        hits = client.post("/search", json={"query": "犬の散歩", "top_k": 1}).json()
        assert hits[0]["id"] == row["id"]
        assert "犬の散歩は朝が気持ちいい" in hits[0]["content"]

    def test_top_kが0以下は422(self, client: TestClient) -> None:
        assert client.post("/search", json={"query": "a", "top_k": 0}).status_code == 422
