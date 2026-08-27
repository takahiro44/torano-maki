"""ingest（テキスト抽出）のテスト。vLLM は呼ばない。"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.models.knowledge import ExtractedKnowledge

_SAMPLE = ExtractedKnowledge(
    title="価格指摘への切り返し",
    situation="他社より高いと指摘された",
    problem="価格が高いことが障壁",
    action="値引きせず比較軸を聞いた",
    lesson="値引きより先に評価軸を確認する",
    knowledge_type="business",
)
_FAKE_VECTOR = [0.0] * 1024


def test_preview_はDBに書かない(client: TestClient) -> None:
    raw = "先日、価格が高いと指摘された。値引きせず比較軸を聞いた。"
    with patch(
        "app.api.ingest.extract_knowledge_with_sources",
        return_value=[(_SAMPLE, raw)],
    ):
        res = client.post("/ingest/text/preview", json={"raw_text": raw})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["saved"] == []
    assert len(body["extracted"]) == 1
    assert "【タイトル】" in body["extracted"][0]["content"]


def test_ingest_text_はdraftでCBR列に保存する(client: TestClient) -> None:
    raw = "先日、価格が高いと指摘された。値引きせず比較軸を聞いた。"
    with (
        patch(
            "app.services.extraction.extract_knowledge_with_sources",
            return_value=[(_SAMPLE, raw)],
        ),
        patch("app.services.extraction.generate_embedding", return_value=_FAKE_VECTOR),
    ):
        res = client.post("/ingest/text", json={"raw_text": raw})
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["saved"]) == 1
    saved = body["saved"][0]
    assert saved["status"] == "draft"
    assert saved["title"] == "価格指摘への切り返し"
    assert saved["lesson"] == "値引きより先に評価軸を確認する"
    assert saved["action"] == "値引きせず比較軸を聞いた"
    assert saved["problem"] == "価格が高いことが障壁"

    listed = client.get("/knowledge", params={"status": "draft"}).json()
    assert any(row["id"] == saved["id"] for row in listed)

    evidence = client.get(f"/knowledge/{saved['id']}/evidence").json()
    assert len(evidence) == 1
    assert evidence[0]["utterances"][0]["content"] == raw


def test_preview_LLM未設定は503(client: TestClient) -> None:
    from app.services.extraction import LlmNotConfiguredError

    with patch(
        "app.api.ingest.extract_knowledge_with_sources",
        side_effect=LlmNotConfiguredError("未設定"),
    ):
        res = client.post("/ingest/text/preview", json={"raw_text": "十文字は超える入力です"})
    assert res.status_code == 503
