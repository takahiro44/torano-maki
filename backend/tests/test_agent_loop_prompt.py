"""SYSTEM_PROMPT に案件分離ルールが入っていること。"""

from app.services.agent_loop import SYSTEM_PROMPT


def test_system_promptに案件分離の指示がある() -> None:
    assert "案件の分離" in SYSTEM_PROMPT
    assert "混ぜない" in SYSTEM_PROMPT
    assert "別案件" in SYSTEM_PROMPT or "分けて" in SYSTEM_PROMPT
