"""ロープレの入出力契約。

**Qwen が壊れた形を返したときに、静かに通さないこと**を確認する。
余分なキーや0件のrubricをそのまま受けると、シナリオに無い設定や
評価対象のすり替えが画面まで届く。
"""

import pytest
from pydantic import ValidationError

from app.models.roleplay import (
    CATEGORY_LABELS,
    CATEGORY_QUERIES,
    GeneratedFeedback,
    InputMode,
    LearnerTurnRequest,
    RoleplayCategory,
    RoleplayScenario,
    RoleplaySessionCreate,
    RubricVerdict,
)


def _scenario_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "値引き要求の背景を確認する",
        "situation": "在庫管理システムの提案中。顧客は他社より高いと感じている。",
        "learner_goal": "値引きに答える前に、価格が問題になる背景を質問する。",
        "customer_persona": "慎重な業務部長。費用対効果を重視する。",
        "opening_line": "他社より二十万円高いですよね。値引きできませんか。",
        "max_turns": 2,
        "rubric": [{"key": "clarify_reason", "label": "値引き要求の背景を確認した"}],
    }
    payload.update(overrides)
    return payload


def test_シナリオは余分なキーを拒否する() -> None:
    # Qwen が knowledge_id を勝手に付けても、出典として通してはいけない
    with pytest.raises(ValidationError):
        RoleplayScenario.model_validate(
            _scenario_payload(knowledge_id="00000000-0000-0000-0000-000000000000")
        )


def test_シナリオはrubricが0件だと拒否する() -> None:
    # 観点が無いとフィードバックの判定対象が消え、総合点だけが残る
    with pytest.raises(ValidationError):
        RoleplayScenario.model_validate(_scenario_payload(rubric=[]))


def test_シナリオはmax_turnsの上限を超えると拒否する() -> None:
    with pytest.raises(ValidationError):
        RoleplayScenario.model_validate(_scenario_payload(max_turns=10))


def test_シナリオは最小構成なら通る() -> None:
    scenario = RoleplayScenario.model_validate(_scenario_payload())
    assert scenario.max_turns == 2
    assert scenario.rubric[0].key == "clarify_reason"


def test_クライアントはgeneratedを名乗れない() -> None:
    # AIが作った発言を後輩の回答として保存できてはいけない
    with pytest.raises(ValidationError):
        LearnerTurnRequest(content="背景を教えてください", input_mode=InputMode.GENERATED)


def test_後輩の回答は空文字を拒否する() -> None:
    with pytest.raises(ValidationError):
        LearnerTurnRequest(content="")


def test_テキスト回答と音声回答はどちらも受け付ける() -> None:
    assert LearnerTurnRequest(content="なぜ高いと感じますか").input_mode is InputMode.TEXT
    audio = LearnerTurnRequest(content="なぜ高いと感じますか", input_mode=InputMode.AUDIO)
    assert audio.input_mode is InputMode.AUDIO


def test_開始条件が何も無いセッションは作れない() -> None:
    # 何を練習したいのか決まらず、検索クエリも作れない
    with pytest.raises(ValidationError):
        RoleplaySessionCreate()


def test_カテゴリだけでもセッションを開始できる() -> None:
    payload = RoleplaySessionCreate(category=RoleplayCategory.PRICE_OBJECTION)
    assert payload.category is RoleplayCategory.PRICE_OBJECTION
    assert payload.max_turns == 2


def test_全カテゴリに表示名と検索クエリがある() -> None:
    # 片方だけ増やすと、選べるのに検索できない場面が生まれる
    for category in RoleplayCategory:
        assert CATEGORY_LABELS[category]
        assert CATEGORY_QUERIES[category]


def test_カテゴリの検索クエリはカテゴリ名より長い() -> None:
    # 「値引き」の2文字では lexical 検索がほぼ立たないため展開している
    for category in RoleplayCategory:
        assert len(CATEGORY_QUERIES[category]) > len(CATEGORY_LABELS[category])


def test_フィードバックは不正な判定値を拒否する() -> None:
    with pytest.raises(ValidationError):
        GeneratedFeedback.model_validate(
            {
                "rubric_results": [
                    {"key": "clarify_reason", "verdict": "perfect", "comment": "よい"}
                ],
                "next_phrase": "背景を教えてください",
                "focus_next_try": "先に理由を聞く",
            }
        )


def test_フィードバックは判定が0件だと拒否する() -> None:
    with pytest.raises(ValidationError):
        GeneratedFeedback.model_validate(
            {
                "rubric_results": [],
                "next_phrase": "背景を教えてください",
                "focus_next_try": "先に理由を聞く",
            }
        )


def test_フィードバックは強みと改善点が空でも通る() -> None:
    # 1往復では褒める点が無いこともある。無理に埋めさせない
    feedback = GeneratedFeedback.model_validate(
        {
            "rubric_results": [
                {"key": "clarify_reason", "verdict": "not_met", "comment": "理由を聞いていない"}
            ],
            "next_phrase": "差額が気になる理由を教えていただけますか",
            "focus_next_try": "値引きに答える前に質問する",
        }
    )
    assert feedback.strengths == []
    assert feedback.rubric_results[0].verdict is RubricVerdict.NOT_MET
    # label は Qwen に書かせず、サーバが rubric から埋める
    assert feedback.rubric_results[0].label == ""
