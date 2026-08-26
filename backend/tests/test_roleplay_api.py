"""ロープレAPIの入口。

**確認したいのは失敗の伝わり方。** DGXが落ちている・ナレッジに根拠が無い・
発言回数を使い切った、はどれも利用者の操作が悪いわけではない。
画面が案内を出し分けられるよう、原因ごとに違うステータスへ振り分ける。
"""

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.roleplay import CATEGORY_LABELS, LearnerTurnRequest, RoleplaySessionCreate
from app.services.llm_client import LlmNotConfiguredError, LlmRequestError
from app.services.roleplay import RoleplayError, add_learner_turn, create_feedback, create_session
from tests.test_roleplay_service import (
    _FEEDBACK_JSON,
    _SCENARIO_JSON,
    _llm_body,
    _make_knowledge,
)


def _reply_body(text: str) -> dict[str, object]:
    return _llm_body({"content": text})


def _session_in_db(db: Session):
    """LLMを差し替えて、実際のセッションを1件作る。"""
    knowledge = _make_knowledge(db)
    hits = [SimpleNamespace(id=knowledge.id)]
    with (
        patch("app.services.roleplay.search_knowledge", return_value=hits),
        patch("app.services.roleplay.chat_completion", return_value=_llm_body(_SCENARIO_JSON)),
    ):
        return create_session(db, RoleplaySessionCreate(query="値引きを求められたら"))


def test_場面カテゴリの一覧を返す(client: TestClient) -> None:
    response = client.get("/roleplay/categories")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == len(CATEGORY_LABELS)
    assert {"key": "price_objection", "label": "値引き"} in body


def test_開始条件が無いリクエストは422(client: TestClient) -> None:
    assert client.post("/roleplay/sessions", json={}).status_code == 422


def test_generatedを名乗る回答は422(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    response = client.post(
        f"/roleplay/sessions/{session.id}/turns/text",
        json={"content": "AIのふりをした回答", "input_mode": "generated"},
    )
    assert response.status_code == 422


def test_根拠付きナレッジが無ければ422(client: TestClient) -> None:
    error = RoleplayError("no_evidence", "根拠となる発話がありません")
    with patch("app.api.roleplay.create_session", side_effect=error):
        response = client.post("/roleplay/sessions", json={"query": "値引き"})
    assert response.status_code == 422
    assert "根拠" in response.json()["detail"]


def test_LLM未設定は503(client: TestClient) -> None:
    with patch("app.api.roleplay.create_session", side_effect=LlmNotConfiguredError("未設定")):
        response = client.post("/roleplay/sessions", json={"query": "値引き"})
    assert response.status_code == 503


def test_LLMへ到達できなければ502(client: TestClient) -> None:
    with patch("app.api.roleplay.create_session", side_effect=LlmRequestError("接続できません")):
        response = client.post("/roleplay/sessions", json={"query": "値引き"})
    assert response.status_code == 502


def test_存在しないセッションは404(client: TestClient) -> None:
    assert client.get(f"/roleplay/sessions/{uuid4()}").status_code == 404


def test_セッションを作って回答し振り返るまで通る(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)

    read = client.get(f"/roleplay/sessions/{session.id}")
    assert read.status_code == 200
    assert read.json()["scenario"]["opening_line"] == _SCENARIO_JSON["opening_line"]
    assert read.json()["remaining_learner_turns"] == 2

    with patch("app.services.roleplay.chat_completion", return_value=_reply_body("理由ですか。")):
        turn = client.post(
            f"/roleplay/sessions/{session.id}/turns/text",
            json={"content": "なぜ高いと感じられますか"},
        )
    assert turn.status_code == 200
    assert [t["role"] for t in turn.json()["turns"]] == ["customer", "learner", "customer"]

    with patch("app.services.roleplay.chat_completion", return_value=_llm_body(_FEEDBACK_JSON)):
        done = client.post(f"/roleplay/sessions/{session.id}/feedback")
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "completed"
    assert body["feedback"]["focus_next_try"] == _FEEDBACK_JSON["focus_next_try"]
    # 出典に元の発話と時刻が含まれること（MVP完了条件）
    assert body["references"][0]["utterances"][0]["start_sec"] == 30.0
    assert body["references"][0]["limitations"]


def test_終了済みセッションへの回答は409(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    with patch("app.services.roleplay.chat_completion", return_value=_reply_body("なるほど。")):
        add_learner_turn(db, session, LearnerTurnRequest(content="なぜ高いと感じますか"))
    with patch("app.services.roleplay.chat_completion", return_value=_llm_body(_FEEDBACK_JSON)):
        create_feedback(db, session)

    response = client.post(
        f"/roleplay/sessions/{session.id}/turns/text", json={"content": "まだ話したい"}
    )
    assert response.status_code == 409


def test_発言回数を使い切ったら409(client: TestClient, db: Session) -> None:
    knowledge = _make_knowledge(db)
    hits = [SimpleNamespace(id=knowledge.id)]
    with (
        patch("app.services.roleplay.search_knowledge", return_value=hits),
        patch("app.services.roleplay.chat_completion", return_value=_llm_body(_SCENARIO_JSON)),
    ):
        session = create_session(db, RoleplaySessionCreate(query="値引き", max_turns=1))

    with patch("app.services.roleplay.chat_completion", return_value=_reply_body("なるほど。")):
        first = client.post(
            f"/roleplay/sessions/{session.id}/turns/text", json={"content": "1回目"}
        )
        assert first.status_code == 200
        second = client.post(
            f"/roleplay/sessions/{session.id}/turns/text", json={"content": "2回目"}
        )
    assert second.status_code == 409


def test_再挑戦は待たずに同じ場面を作る(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    # LLMを差し替えずに201が返ることが「生成し直していない」ことの確認になる
    response = client.post(f"/roleplay/sessions/{session.id}/retry")

    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] != str(session.id)
    assert body["scenario"]["title"] == _SCENARIO_JSON["title"]
    assert body["learner_turns_used"] == 0


def test_ロープレのシナリオはOpenAPIに出る(client: TestClient) -> None:
    # フロント担当が型をここから起こすため（CLAUDE.md 5章）
    schema = json.loads(client.get("/openapi.json").text)
    assert "RoleplayScenario" in schema["components"]["schemas"]
    assert "/roleplay/sessions" in schema["paths"]
