"""ロープレのサービス層。

**確認したいのは「根拠と状態の守り」。** 生成の文章品質はテストできないが、
根拠の無いナレッジで練習が始まらないこと、発言回数の上限が守られること、
出典がサーバ側の記録からしか作られないことは機械的に確認できる。

vLLM へは接続しない。DGXは貸し出し機で常時使えないため、
LLM 呼び出しは差し替えてサービスの分岐だけを見る。
"""

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.roleplay import (
    InputMode,
    LearnerTurnRequest,
    RoleplayScenario,
    RoleplaySessionCreate,
    RubricResult,
    RubricVerdict,
    SessionStatus,
    TurnRole,
    UsageType,
)
from app.models.tables import (
    DataSourceTable,
    KnowledgeEvidenceTable,
    KnowledgeUnitTable,
    RoleplayFeedbackTable,
    RoleplayTurnTable,
    UtteranceSegmentTable,
)
from app.services.roleplay import (
    RoleplayError,
    RoleplayGenerationError,
    _align_rubric_results,
    _parse_json_object,
    add_learner_turn,
    build_session_view,
    create_feedback,
    create_session,
    retry_session,
    scenario_of,
    select_knowledge,
)

_SCENARIO_JSON = {
    "title": "値引き要求の背景を確認する",
    "situation": "販売管理システムの提案中。顧客は他社より高いと感じている。",
    "learner_goal": "値引きに答える前に、価格が問題になる背景を質問する。",
    "customer_persona": "慎重な業務部長。費用対効果を重視する。",
    "opening_line": "他社より高いですよね。値引きできませんか。",
    "max_turns": 2,
    "rubric": [
        {"key": "clarify_reason", "label": "値引き要求の背景を確認した"},
        {"key": "connect_value", "label": "顧客課題と価値を結びつけた"},
    ],
}

_FEEDBACK_JSON = {
    "rubric_results": [
        {"key": "clarify_reason", "verdict": "met", "comment": "背景を先に聞けている"},
        {"key": "connect_value", "verdict": "partial", "comment": "価値の説明が一般論だった"},
    ],
    "strengths": ["値引きに即答しなかった"],
    "improvements": ["顧客の業務課題に紐づけて説明する"],
    "next_phrase": "差額が気になる理由を教えていただけますか",
    "focus_next_try": "値引きに答える前に必ず1つ質問する",
}


def _llm_body(payload: dict[str, object]) -> dict[str, object]:
    """vLLM の応答の形。content は文字列で返る。"""
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


def _make_knowledge(
    db: Session, *, with_evidence: bool = True, title: str = "値引きへの切り返し"
) -> KnowledgeUnitTable:
    """根拠つきの確認済みナレッジを1件作る。"""
    source = DataSourceTable(source_type="audio", file_name="roleplay-test.wav")
    db.add(source)
    db.flush()

    segments = [
        UtteranceSegmentTable(
            data_source_id=source.id,
            sequence_no=i,
            speaker="customer" if i % 2 else "salesperson",
            start_sec=float(i * 10),
            end_sec=float(i * 10 + 9),
            content=f"発言{i}",
        )
        for i in range(1, 7)
    ]
    db.add_all(segments)
    db.flush()

    knowledge = KnowledgeUnitTable(
        data_source_id=source.id,
        title=title,
        situation="他社より高いと言われた",
        action="値引きの前に背景を質問した",
        limitations="承認済みの価格条件がある場合は別判断",
        status="confirmed",
    )
    db.add(knowledge)
    db.flush()

    if with_evidence:
        db.add(
            KnowledgeEvidenceTable(
                knowledge_id=knowledge.id,
                start_utterance_id=segments[2].id,
                end_utterance_id=segments[3].id,
            )
        )
        db.flush()
    return knowledge


def _create_session_with_llm(
    db: Session, payload: RoleplaySessionCreate, knowledge_ids: list[UUID]
):
    """検索とLLMを差し替えてセッションを作る。"""
    hits = [SimpleNamespace(id=kid) for kid in knowledge_ids]
    with (
        patch("app.services.roleplay.search_knowledge", return_value=hits),
        patch("app.services.roleplay.chat_completion", return_value=_llm_body(_SCENARIO_JSON)),
    ):
        return create_session(db, payload)


# ---------------------------------------------------------------------------
# 根拠の選定
# ---------------------------------------------------------------------------


def test_根拠の無いナレッジを指定すると開始できない(db: Session) -> None:
    knowledge = _make_knowledge(db, with_evidence=False)
    payload = RoleplaySessionCreate(knowledge_id=knowledge.id)

    with patch("app.services.roleplay.search_knowledge", return_value=[]):
        with pytest.raises(RoleplayError) as exc:
            select_knowledge(db, payload)
    assert exc.value.code == "no_evidence"


def test_根拠の無い検索結果は候補から外れる(db: Session) -> None:
    without = _make_knowledge(db, with_evidence=False, title="根拠なし")
    with_evidence = _make_knowledge(db, title="根拠あり")
    hits = [SimpleNamespace(id=without.id), SimpleNamespace(id=with_evidence.id)]

    with patch("app.services.roleplay.search_knowledge", return_value=hits):
        selected = select_knowledge(db, RoleplaySessionCreate(query="値引き"))

    assert [item.context.knowledge.id for item in selected] == [with_evidence.id]
    assert selected[0].usage_type is UsageType.PRIMARY


def test_確認済みでないナレッジでは練習を始められない(db: Session) -> None:
    knowledge = _make_knowledge(db)
    knowledge.status = "draft"
    db.flush()

    with patch("app.services.roleplay.search_knowledge", return_value=[]):
        with pytest.raises(RoleplayError) as exc:
            select_knowledge(db, RoleplaySessionCreate(knowledge_id=knowledge.id))
    assert exc.value.code == "no_evidence"


def test_指定したナレッジがprimaryになる(db: Session) -> None:
    primary = _make_knowledge(db, title="指定したもの")
    other = _make_knowledge(db, title="検索で出たもの")

    with patch(
        "app.services.roleplay.search_knowledge", return_value=[SimpleNamespace(id=other.id)]
    ):
        selected = select_knowledge(db, RoleplaySessionCreate(knowledge_id=primary.id))

    assert selected[0].context.knowledge.id == primary.id
    assert selected[0].usage_type is UsageType.PRIMARY
    assert selected[1].usage_type is UsageType.SUPPORTING


# ---------------------------------------------------------------------------
# セッションの生成
# ---------------------------------------------------------------------------


def test_セッション作成でシナリオと最初の発言が保存される(db: Session) -> None:
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(
        db, RoleplaySessionCreate(query="値引きを求められたら"), [knowledge.id]
    )

    assert session.status == SessionStatus.ACTIVE
    scenario = scenario_of(session)
    assert scenario.title == _SCENARIO_JSON["title"]

    view = build_session_view(db, session)
    assert len(view.turns) == 1
    assert view.turns[0].role is TurnRole.CUSTOMER
    assert view.turns[0].input_mode is InputMode.GENERATED
    assert view.turns[0].content == _SCENARIO_JSON["opening_line"]
    assert view.remaining_learner_turns == 2


def test_max_turnsはサーバの指定で上書きされる(db: Session) -> None:
    # ラウンドロビン用の1往復モードをモデルに覆されると時間管理ができない
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(
        db, RoleplaySessionCreate(query="値引き", max_turns=1), [knowledge.id]
    )
    assert scenario_of(session).max_turns == 1


def test_出典はセッションに紐づけたナレッジだけから作られる(db: Session) -> None:
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(db, RoleplaySessionCreate(query="値引き"), [knowledge.id])

    view = build_session_view(db, session)
    assert [ref.knowledge_id for ref in view.references] == [knowledge.id]
    assert view.references[0].limitations == "承認済みの価格条件がある場合は別判断"
    # 根拠発話は時刻つきで辿れること（計画書6章）
    assert [u.sequence_no for u in view.references[0].utterances] == [3, 4]
    assert view.references[0].utterances[0].start_sec == 30.0


def test_シナリオが契約に合わなければセッションを作らない(db: Session) -> None:
    knowledge = _make_knowledge(db)
    broken = {**_SCENARIO_JSON, "rubric": []}
    # 全件数ではなく増分で見る。開発用DBには実際に練習した記録が残っており、
    # 「0件であること」を期待すると、機能を使うたびにテストが落ちる
    before = db.query(RoleplayTurnTable).count()

    with (
        patch(
            "app.services.roleplay.search_knowledge",
            return_value=[SimpleNamespace(id=knowledge.id)],
        ),
        patch("app.services.roleplay.chat_completion", return_value=_llm_body(broken)),
    ):
        with pytest.raises(RoleplayGenerationError):
            create_session(db, RoleplaySessionCreate(query="値引き"))

    # 生成に失敗したのに空のセッションが残ってはいけない
    assert db.query(RoleplayTurnTable).count() == before


# ---------------------------------------------------------------------------
# ターンの進行
# ---------------------------------------------------------------------------


def _reply_body(text: str) -> dict[str, object]:
    return _llm_body({"content": text})


def test_回答すると顧客の返答が続く(db: Session) -> None:
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(db, RoleplaySessionCreate(query="値引き"), [knowledge.id])

    with patch("app.services.roleplay.chat_completion", return_value=_reply_body("理由ですか。")):
        add_learner_turn(db, session, LearnerTurnRequest(content="なぜ高いと感じますか"))

    view = build_session_view(db, session)
    assert [turn.role for turn in view.turns] == [
        TurnRole.CUSTOMER,
        TurnRole.LEARNER,
        TurnRole.CUSTOMER,
    ]
    assert view.turns[1].input_mode is InputMode.TEXT
    assert view.learner_turns_used == 1
    assert view.remaining_learner_turns == 1


def test_上限を超えて発言できない(db: Session) -> None:
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(
        db, RoleplaySessionCreate(query="値引き", max_turns=1), [knowledge.id]
    )

    with patch("app.services.roleplay.chat_completion", return_value=_reply_body("なるほど。")):
        add_learner_turn(db, session, LearnerTurnRequest(content="1回目"))
        with pytest.raises(RoleplayError) as exc:
            add_learner_turn(db, session, LearnerTurnRequest(content="2回目"))

    assert exc.value.code == "max_turns_reached"
    assert build_session_view(db, session).learner_turns_used == 1


def test_顧客役の生成に失敗したら回答も保存しない(db: Session) -> None:
    # 返事の来ない発言が残ると、利用者は同じ回答を送り直せなくなる
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(db, RoleplaySessionCreate(query="値引き"), [knowledge.id])

    with patch("app.services.roleplay.chat_completion", return_value=_llm_body({"bad": "shape"})):
        with pytest.raises(RoleplayGenerationError):
            add_learner_turn(db, session, LearnerTurnRequest(content="なぜ高いと感じますか"))

    assert build_session_view(db, session).learner_turns_used == 0


def test_終了後のセッションには追加入力できない(db: Session) -> None:
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(db, RoleplaySessionCreate(query="値引き"), [knowledge.id])

    with patch("app.services.roleplay.chat_completion", return_value=_reply_body("なるほど。")):
        add_learner_turn(db, session, LearnerTurnRequest(content="なぜ高いと感じますか"))
    with patch("app.services.roleplay.chat_completion", return_value=_llm_body(_FEEDBACK_JSON)):
        create_feedback(db, session)

    with pytest.raises(RoleplayError) as exc:
        add_learner_turn(db, session, LearnerTurnRequest(content="まだ話したい"))
    assert exc.value.code == "session_not_active"


# ---------------------------------------------------------------------------
# フィードバック
# ---------------------------------------------------------------------------


def test_回答が無ければ振り返りを作れない(db: Session) -> None:
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(db, RoleplaySessionCreate(query="値引き"), [knowledge.id])

    with pytest.raises(RoleplayError) as exc:
        create_feedback(db, session)
    assert exc.value.code == "no_learner_turn"


def test_振り返りでactiveからcompletedへ遷移する(db: Session) -> None:
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(db, RoleplaySessionCreate(query="値引き"), [knowledge.id])

    with patch("app.services.roleplay.chat_completion", return_value=_reply_body("なるほど。")):
        add_learner_turn(db, session, LearnerTurnRequest(content="なぜ高いと感じますか"))
    with patch("app.services.roleplay.chat_completion", return_value=_llm_body(_FEEDBACK_JSON)):
        create_feedback(db, session)

    view = build_session_view(db, session)
    assert view.status is SessionStatus.COMPLETED
    assert view.completed_at is not None
    assert view.feedback is not None
    assert view.feedback.next_phrase == _FEEDBACK_JSON["next_phrase"]
    # label はシナリオの rubric から埋める
    assert view.feedback.rubric_results[0].label == "値引き要求の背景を確認した"


def test_振り返りは二度目でも作り直さない(db: Session) -> None:
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(db, RoleplaySessionCreate(query="値引き"), [knowledge.id])

    with patch("app.services.roleplay.chat_completion", return_value=_reply_body("なるほど。")):
        add_learner_turn(db, session, LearnerTurnRequest(content="なぜ高いと感じますか"))
    with patch("app.services.roleplay.chat_completion", return_value=_llm_body(_FEEDBACK_JSON)):
        first = create_feedback(db, session)
    # 2度目はLLMを呼ばない。呼べば差し替えていないので接続エラーになる
    second = create_feedback(db, session)

    assert first.session_id == second.session_id
    assert db.query(RoleplayFeedbackTable).filter_by(session_id=session.id).count() == 1


def test_シナリオに無い観点は講評から落とす() -> None:
    scenario = RoleplayScenario.model_validate(_SCENARIO_JSON)
    results = [
        RubricResult(key="clarify_reason", verdict=RubricVerdict.MET, comment="聞けている"),
        RubricResult(key="invented_key", verdict=RubricVerdict.MET, comment="捏造された観点"),
    ]

    aligned = _align_rubric_results(scenario, results)

    assert [item.key for item in aligned] == ["clarify_reason"]
    assert aligned[0].label == "値引き要求の背景を確認した"


def test_観点が1つも対応しなければ講評を通さない() -> None:
    scenario = RoleplayScenario.model_validate(_SCENARIO_JSON)
    results = [RubricResult(key="unknown", verdict=RubricVerdict.MET, comment="捏造された観点")]

    with pytest.raises(RoleplayGenerationError):
        _align_rubric_results(scenario, results)


# ---------------------------------------------------------------------------
# 再挑戦
# ---------------------------------------------------------------------------


def test_再挑戦は同じシナリオで新しいセッションを作る(db: Session) -> None:
    knowledge = _make_knowledge(db)
    session = _create_session_with_llm(db, RoleplaySessionCreate(query="値引き"), [knowledge.id])
    with patch("app.services.roleplay.chat_completion", return_value=_reply_body("なるほど。")):
        add_learner_turn(db, session, LearnerTurnRequest(content="1回目の回答"))

    # LLMを差し替えずに呼べることが「作り直していない」ことの証明になる
    retried = retry_session(db, session)

    assert retried.id != session.id
    assert scenario_of(retried).title == scenario_of(session).title
    view = build_session_view(db, retried)
    assert view.learner_turns_used == 0
    assert [ref.knowledge_id for ref in view.references] == [knowledge.id]


# ---------------------------------------------------------------------------
# 応答の後始末
# ---------------------------------------------------------------------------


def test_コードフェンス付きの応答からJSONを取り出す() -> None:
    raw = '```json\n{"content": "値引きは難しいですか"}\n```'
    assert _parse_json_object(raw) == {"content": "値引きは難しいですか"}


def test_JSONでない応答は握り潰さない() -> None:
    with pytest.raises(RoleplayGenerationError):
        _parse_json_object("承知しました。シナリオを作ります。")


def test_存在しないセッションIDは業務エラーになる(db: Session) -> None:
    from app.services.roleplay import get_session

    with pytest.raises(RoleplayError) as exc:
        get_session(db, uuid4())
    assert exc.value.code == "session_not_found"
