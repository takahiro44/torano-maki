"""AIと一緒にナレッジを直す処理のテスト。vLLM は呼ばない。"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.models.knowledge import KnowledgeDraft, KnowledgeRefineRequest
from app.services.llm_client import LlmNotConfiguredError, LlmRequestError
from app.services.refine import (
    build_refine_messages,
    changed_field_names,
    refine_knowledge,
)

_DRAFT = {
    "title": "価格指摘への切り返し",
    "situation": "他社より高いと指摘された",
    "problem": "価格が高いことが障壁",
    "judgment": None,
    "action": "値引きせず比較軸を聞いた",
    "reasoning": None,
    "outcome": None,
    "lesson": "値引きより先に評価軸を確認する",
    "applicable_situations": None,
    "limitations": None,
    "industry": None,
    "product": None,
    "sales_stage": None,
}


def _llm_body(comment: str, proposal: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {"message": {"content": json.dumps({"comment": comment, "proposal": proposal})}}
        ]
    }


def test_変わった項目はLLMの申告ではなく値の比較で決まる() -> None:
    proposal = {**_DRAFT, "situation": "田中製作所から他社より300万円高いと指摘された"}
    request = KnowledgeRefineRequest(
        draft=KnowledgeDraft.model_validate(_DRAFT), instruction="金額と社名を残して"
    )
    with patch(
        "app.services.refine.chat_completion",
        return_value=_llm_body("社名と金額を状況に戻しました", proposal),
    ):
        result = refine_knowledge(request)

    assert result.changed_fields == ["situation"]
    assert result.proposal.situation == "田中製作所から他社より300万円高いと指摘された"
    assert result.proposal.lesson == _DRAFT["lesson"]


def test_空文字とnullは同じ未記入として扱う() -> None:
    """LLM が null の項目を空文字で返してきても「直した」ことにしない。"""
    proposal = {**_DRAFT, "judgment": "   "}
    request = KnowledgeRefineRequest(
        draft=KnowledgeDraft.model_validate(_DRAFT), instruction="そのままで"
    )
    with patch(
        "app.services.refine.chat_completion", return_value=_llm_body("変更ありません", proposal)
    ):
        result = refine_knowledge(request)

    assert result.changed_fields == []
    assert result.proposal.judgment is None


def test_解釈できない応答は握り潰さず送出する() -> None:
    """「変更なし」で返すと、失敗に気づかないまま人が保存してしまう。"""
    request = KnowledgeRefineRequest(
        draft=KnowledgeDraft.model_validate(_DRAFT), instruction="短くして"
    )
    with patch(
        "app.services.refine.chat_completion",
        return_value={"choices": [{"message": {"content": "すみません、わかりません"}}]},
    ):
        with pytest.raises(LlmRequestError):
            refine_knowledge(request)


def test_コードフェンス付きの応答も読める() -> None:
    proposal = {**_DRAFT, "lesson": "価格を指摘されたら比較軸を先に聞く"}
    fenced = "```json\n" + json.dumps({"comment": "短くしました", "proposal": proposal}) + "\n```"
    request = KnowledgeRefineRequest(
        draft=KnowledgeDraft.model_validate(_DRAFT), instruction="短くして"
    )
    with patch(
        "app.services.refine.chat_completion",
        return_value={"choices": [{"message": {"content": fenced}}]},
    ):
        result = refine_knowledge(request)

    assert result.changed_fields == ["lesson"]


def test_プロンプトには今の値と原文と指示が入る() -> None:
    request = KnowledgeRefineRequest(
        draft=KnowledgeDraft.model_validate(_DRAFT),
        instruction="学びを次の行動として書き直して",
        history=[{"role": "user", "content": "もっと具体的に"}],
        source_text="先日、田中製作所様との商談で…",
    )
    messages = build_refine_messages(request)

    assert messages[0]["role"] == "system"
    # 履歴は現在の値より前に置く。いちばん新しい値を最後に読ませたいため
    assert messages[1] == {"role": "user", "content": "もっと具体的に"}
    last = messages[-1]["content"]
    assert "値引きせず比較軸を聞いた" in last
    assert "田中製作所様との商談" in last
    assert "学びを次の行動として書き直して" in last
    # 未記入の項目も省かない。省くとモデルが項目自体を落とす
    assert "（未記入）" in last


def test_原文が無くてもプロンプトが成立する() -> None:
    request = KnowledgeRefineRequest(
        draft=KnowledgeDraft.model_validate(_DRAFT), instruction="短くして"
    )
    assert "原文は残っていません" in build_refine_messages(request)[-1]["content"]


def test_未設定なら503で返る(client: TestClient) -> None:
    with patch(
        "app.api.knowledge.refine_knowledge",
        side_effect=LlmNotConfiguredError("BASE_URL / MODEL_NAME が未設定"),
    ):
        res = client.post("/knowledge/refine", json={"draft": _DRAFT, "instruction": "短くして"})
    assert res.status_code == 503, res.text


def test_到達できなければ502で返る(client: TestClient) -> None:
    with patch(
        "app.api.knowledge.refine_knowledge", side_effect=LlmRequestError("vLLM に接続できません")
    ):
        res = client.post("/knowledge/refine", json={"draft": _DRAFT, "instruction": "短くして"})
    assert res.status_code == 502, res.text


def test_refine_はDBに書かない(client: TestClient) -> None:
    """相談は提案を返すだけ。保存するかは人が決める。"""
    before = client.get("/knowledge/count").json()["total"]
    proposal = {**_DRAFT, "lesson": "比較軸を先に聞く"}
    with patch(
        "app.services.refine.chat_completion",
        return_value=_llm_body("短くしました", proposal),
    ):
        res = client.post("/knowledge/refine", json={"draft": _DRAFT, "instruction": "短くして"})
    assert res.status_code == 200, res.text
    assert res.json()["changed_fields"] == ["lesson"]
    assert client.get("/knowledge/count").json()["total"] == before


def test_タイトルが長すぎても相談ごと失敗させない() -> None:
    long_title = "あ" * 140
    proposal = {**_DRAFT, "title": long_title}
    request = KnowledgeRefineRequest(
        draft=KnowledgeDraft.model_validate(_DRAFT), instruction="タイトルを詳しく"
    )
    with patch(
        "app.services.refine.chat_completion", return_value=_llm_body("直しました", proposal)
    ):
        result = refine_knowledge(request)
    assert len(result.proposal.title) == 100


def test_changed_field_namesは順序を保つ() -> None:
    before = KnowledgeDraft.model_validate(_DRAFT)
    after = KnowledgeDraft.model_validate(
        {**_DRAFT, "title": "価格指摘の切り返し", "lesson": "比較軸を先に聞く"}
    )
    assert changed_field_names(before, after) == ["title", "lesson"]
