"""上司レビュー機能のテスト。vLLM/埋め込みは呼ばない。"""

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.chat_review import ChatReviewSummary
from app.models.knowledge import ExtractedKnowledge

_SAMPLE_MESSAGES = [
    {"role": "user", "content": "在庫が合わない時はどう対応しますか？"},
    {"role": "assistant", "content": "蓄積されたナレッジには該当する情報がありません。"},
]

_SAMPLE_SUMMARY = ChatReviewSummary(
    summary="在庫不一致時の対応について質問したが、該当ナレッジが無かった。",
    understood_points=["在庫不一致自体はナレッジ検索で確認できる"],
    knowledge_gaps=["在庫不一致が起きたときの謝罪対応の型が無い"],
)

_SAMPLE_KNOWLEDGE = ExtractedKnowledge(
    title="在庫不一致時の謝罪対応",
    situation="出荷後に在庫差異が発覚した",
    problem="顧客への説明が後手に回った",
    action="差異理由を先に伝え、代替納期を提示した",
    lesson="差異が分かった時点で先に連絡する",
)
_FAKE_VECTOR = [0.0] * 1024


def test_summarizeはDBに書き込まない(client: TestClient, db: Session) -> None:
    with patch(
        "app.api.chat_review.generate_chat_review_summary",
        return_value=_SAMPLE_SUMMARY,
    ):
        res = client.post("/chat-reviews/summarize", json={"messages": _SAMPLE_MESSAGES})
    assert res.status_code == 200, res.text
    assert res.json()["summary"] == _SAMPLE_SUMMARY.summary

    listed = client.get("/chat-reviews").json()
    assert listed == []


def test_送信するとpendingで保存される(client: TestClient) -> None:
    with patch(
        "app.services.chat_review.generate_chat_review_summary",
        return_value=_SAMPLE_SUMMARY,
    ):
        res = client.post("/chat-reviews", json={"messages": _SAMPLE_MESSAGES})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "pending"
    assert body["knowledge_gaps"] == _SAMPLE_SUMMARY.knowledge_gaps
    assert len(body["chat_history"]) == 2

    listed = client.get("/chat-reviews", params={"status": "pending"}).json()
    assert any(row["id"] == body["id"] for row in listed)


def test_上司が回答するとconfirmedのナレッジになる(client: TestClient) -> None:
    with patch(
        "app.services.chat_review.generate_chat_review_summary",
        return_value=_SAMPLE_SUMMARY,
    ):
        created = client.post("/chat-reviews", json={"messages": _SAMPLE_MESSAGES}).json()

    with (
        patch(
            "app.services.extraction.extract_knowledge_with_sources",
            return_value=[(_SAMPLE_KNOWLEDGE, "差異理由を先に伝え、代替納期を提示した")],
        ),
        patch("app.services.extraction.generate_embedding", return_value=_FAKE_VECTOR),
    ):
        res = client.post(
            f"/chat-reviews/{created['id']}/respond",
            json={"response_text": "差異理由を先に伝え、代替納期を提示するとよい"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "answered"
    assert len(body["created_knowledge"]) == 1
    assert body["created_knowledge"][0]["title"] == "在庫不一致時の謝罪対応"

    listed = client.get("/knowledge", params={"status": "confirmed"}).json()
    assert any(row["id"] == body["created_knowledge"][0]["id"] for row in listed)


def test_存在しないレビューへの回答は404(client: TestClient) -> None:
    res = client.post(f"/chat-reviews/{uuid4()}/respond", json={"response_text": "回答"})
    assert res.status_code == 404


def test_回答済みへの再回答は409(client: TestClient) -> None:
    with patch(
        "app.services.chat_review.generate_chat_review_summary",
        return_value=_SAMPLE_SUMMARY,
    ):
        created = client.post("/chat-reviews", json={"messages": _SAMPLE_MESSAGES}).json()

    with (
        patch(
            "app.services.extraction.extract_knowledge_with_sources",
            return_value=[(_SAMPLE_KNOWLEDGE, "差異理由を先に伝え、代替納期を提示した")],
        ),
        patch("app.services.extraction.generate_embedding", return_value=_FAKE_VECTOR),
    ):
        first = client.post(
            f"/chat-reviews/{created['id']}/respond", json={"response_text": "1回目の回答"}
        )
        assert first.status_code == 200, first.text
        second = client.post(
            f"/chat-reviews/{created['id']}/respond", json={"response_text": "2回目の回答"}
        )
    assert second.status_code == 409
