"""スキーマの検証。DBを使わないので速い。"""

import pytest
from pydantic import ValidationError

from app.models.knowledge import KnowledgeCreate, KnowledgeStatus, KnowledgeUpdate


class TestKnowledgeCreate:
    def test_本文だけで登録できる(self) -> None:
        """入力時に構造化を強制しない方針（実装計画 §4）を守れているか。"""
        k = KnowledgeCreate(content="A社はサポートを重視する")
        assert k.status == KnowledgeStatus.CONFIRMED
        assert k.source_type == "manual"

    def test_空文字は弾く(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeCreate(content="")

    @pytest.mark.parametrize("blank", ["   ", "\t", "\n", " 　 "])
    def test_空白だけの本文は弾く(self, blank: str) -> None:
        """min_length だけだと通ってしまい、中身の無いナレッジが登録される。"""
        with pytest.raises(ValidationError):
            KnowledgeCreate(content=blank)

    def test_前後の空白は落とされる(self) -> None:
        assert KnowledgeCreate(content="  A社の話  ").content == "A社の話"


class TestKnowledgeUpdate:
    def test_キーを省略すれば変更しない扱いになる(self) -> None:
        assert KnowledgeUpdate().model_dump(exclude_unset=True) == {}

    def test_指定した項目だけが対象になる(self) -> None:
        changes = KnowledgeUpdate(content="更新後").model_dump(exclude_unset=True)
        assert changes == {"content": "更新後"}

    @pytest.mark.parametrize("field", ["content", "status"])
    def test_明示的なnullは弾く(self, field: str) -> None:
        """null を許すと省略と区別できず、NOT NULL列にNoneが渡って500になる。"""
        with pytest.raises(ValidationError):
            KnowledgeUpdate(**{field: None})

    def test_空白だけの本文への更新は弾く(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeUpdate(content="   ")
