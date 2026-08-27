"""上司レビュー機能のテスト。vLLM/埋め込みは呼ばない。"""

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.chat_review import (
    ChatReviewDetail,
    ChatReviewSummary,
    GapDbState,
    GapDiagnosis,
    GapKnowledgeHit,
)
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
    knowledge_type="business",
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
    assert [g["question"] for g in body["knowledge_gaps"]] == _SAMPLE_SUMMARY.knowledge_gaps
    # ヒアリング無しの経路では照合しない。判定していないことを None で表す
    assert all(g["db_state"] is None for g in body["knowledge_gaps"])
    assert len(body["chat_history"]) == 2

    listed = client.get("/chat-reviews", params={"status": "pending"}).json()
    assert any(row["id"] == body["id"] for row in listed)


def test_ヒアリングを付けると要約を作り直さない(client: TestClient) -> None:
    """本人が読んで答えた文面がそのまま上司に届くこと。

    作り直すと、自己申告がどの論点に対する答えなのか分からなくなる。
    """
    hearing = {
        "learner_name": "山田",
        "summary": "後輩が読んだ要約",
        "understood": [
            {"point": "在庫差異の連絡は早い方がよい", "level": "understood"},
            {"point": "値引きの判断基準", "level": "shaky"},
        ],
        "questions": [
            {"question": "AIが拾った疑問", "source": "agent"},
            {"question": "自分で書いた疑問", "source": "learner"},
        ],
    }
    with (
        patch("app.services.chat_review.generate_chat_review_summary") as summarize,
        patch(
            "app.services.chat_review.match_gap",
            side_effect=lambda _db, gap: GapDiagnosis(gap=gap, db_state=GapDbState.MISSING),
        ),
    ):
        res = client.post("/chat-reviews", json={"messages": _SAMPLE_MESSAGES, "hearing": hearing})
    assert res.status_code == 201, res.text
    summarize.assert_not_called()

    body = res.json()
    assert body["summary"] == "後輩が読んだ要約"
    assert body["learner_name"] == "山田"
    assert [p["level"] for p in body["understood_points"]] == ["understood", "shaky"]
    assert {g["source"] for g in body["knowledge_gaps"]} == {"agent", "learner"}
    assert all(g["db_state"] == "missing" for g in body["knowledge_gaps"])


def test_ナレッジDBの状態はクライアントに言わせない(client: TestClient) -> None:
    """`db_state` はサーバが検索して埋める。送っても弾かれる。"""
    res = client.post(
        "/chat-reviews",
        json={
            "messages": _SAMPLE_MESSAGES,
            "hearing": {
                "summary": "要約",
                "questions": [{"question": "問い", "db_state": "missing"}],
            },
        },
    )
    assert res.status_code == 422


def test_本人が書いた問いもナレッジDBに当てる(client: TestClient) -> None:
    """会話に出てこない問いこそ照合が要る。上司が答えを書く必要の有無が変わる。"""
    hit = GapKnowledgeHit(knowledge_id=uuid4(), title="在庫差異の連絡", semantic_score=0.91)
    with patch(
        "app.services.chat_review.match_gap",
        return_value=GapDiagnosis(
            gap="問い",
            db_state=GapDbState.FOUND_BUT_UNREACHABLE,
            existing_knowledge=[hit],
        ),
    ):
        res = client.post(
            "/chat-reviews",
            json={
                "messages": _SAMPLE_MESSAGES,
                "hearing": {
                    "summary": "要約",
                    "questions": [
                        {"question": "在庫がずれたら誰に先に言う？", "source": "learner"}
                    ],
                },
            },
        )
    assert res.status_code == 201, res.text
    gap = res.json()["knowledge_gaps"][0]
    assert gap["db_state"] == "found_but_unreachable"
    assert gap["existing_knowledge"][0]["title"] == "在庫差異の連絡"


def test_文字列で保存された旧形式のレビューも読める() -> None:
    """JSONB に list[str] が残っている行を、DBを作り直さずに読むため。"""
    detail = ChatReviewDetail.model_validate(
        {
            "id": uuid4(),
            "chat_history": [],
            "summary": "旧形式",
            "understood_points": ["理解できていた事項"],
            "knowledge_gaps": ["埋まらなかった疑問"],
            "status": "pending",
            "created_at": "2026-08-01T00:00:00Z",
        }
    )
    assert detail.understood_points[0].point == "理解できていた事項"
    assert detail.understood_points[0].level == "understood"
    assert detail.knowledge_gaps[0].question == "埋まらなかった疑問"
    assert detail.knowledge_gaps[0].db_state is None


def test_上司が回答すると下書きのナレッジができる(client: TestClient) -> None:
    """**承認まではしない。** 上司が中身を見て承認するまで検索対象にしない
    （ナレッジ登録と同じ扱い）。"""
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
    draft = body["created_knowledge"][0]
    assert draft["title"] == "在庫不一致時の謝罪対応"
    # 画面が引き直さずに確認・修正できるよう、中身ごと返ってくる
    assert draft["status"] == "draft"
    assert draft["action"] == "差異理由を先に伝え、代替納期を提示した"

    listed = client.get("/knowledge", params={"status": "confirmed"}).json()
    assert all(row["id"] != draft["id"] for row in listed)

    # 承認するとナレッジ登録と同じく検索対象になる
    approved = client.patch(f"/knowledge/{draft['id']}", json={"status": "confirmed"})
    assert approved.status_code == 200, approved.text
    listed = client.get("/knowledge", params={"status": "confirmed"}).json()
    assert any(row["id"] == draft["id"] for row in listed)


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
