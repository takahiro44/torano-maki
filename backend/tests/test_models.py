"""スキーマの検証。DBを使わないので速い。"""

import pytest
from pydantic import ValidationError

from app.models.knowledge import (
    ExtractedKnowledge,
    KnowledgeCreate,
    KnowledgeStatus,
    KnowledgeUpdate,
)
from app.services.extraction import (
    build_extraction_payload,
    format_item_as_content,
    split_source_text,
)
from app.services.search_text import generate_search_text


class TestKnowledgeCreate:
    def test_titleだけで登録できる(self) -> None:
        k = KnowledgeCreate(title="A社はサポートを重視する")
        assert k.status == KnowledgeStatus.CONFIRMED
        assert k.knowledge_type == "sales_knowhow"

    def test_空文字は弾く(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeCreate(title="")

    @pytest.mark.parametrize("blank", ["   ", "\t", "\n"])
    def test_空白だけのtitleは弾く(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            KnowledgeCreate(title=blank)

    def test_前後の空白は落とされる(self) -> None:
        assert KnowledgeCreate(title="  A社の話  ").title == "A社の話"


class TestKnowledgeUpdate:
    def test_キーを省略すれば変更しない扱いになる(self) -> None:
        assert KnowledgeUpdate().model_dump(exclude_unset=True) == {}

    def test_指定した項目だけが対象になる(self) -> None:
        changes = KnowledgeUpdate(situation="更新後").model_dump(exclude_unset=True)
        assert changes == {"situation": "更新後"}

    @pytest.mark.parametrize("field", ["title", "status"])
    def test_明示的なnullは弾く(self, field: str) -> None:
        with pytest.raises(ValidationError):
            KnowledgeUpdate(**{field: None})


def test_CBR項目は見出し付きで整形できる() -> None:
    item = ExtractedKnowledge(title="見出し", lesson="学びだけある")
    text = format_item_as_content(item)
    assert "【タイトル】\n見出し" in text
    assert "【学び】\n学びだけある" in text
    assert "【状況】" not in text


def test_search_textはCBRをフラット化する() -> None:
    blob = generate_search_text(
        title="見出し",
        lesson="学びだけある",
        industry="製造業",
    )
    assert blob.startswith("見出し")
    assert "学び: 学びだけある" in blob
    assert "業界: 製造業" in blob


def test_短いメモは1チャンク() -> None:
    assert split_source_text("担当が来月から変わる") == ["担当が来月から変わる"]


def test_長文は複数チャンクに割る() -> None:
    text = ("段落です。\n\n" * 800).strip()
    chunks = split_source_text(text)
    assert len(chunks) >= 2
    assert all(len(c) <= 4500 for c in chunks)


def test_抽出リクエストはvLLMのstructured_outputsを使う() -> None:
    payload = build_extraction_payload("価格が高いと言われた", model="Qwen3.8-27B-NVFP4")
    assert payload["model"] == "Qwen3.8-27B-NVFP4"
    assert payload["structured_outputs"]["json"]["title"] == "ExtractionResult"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["chat_template_kwargs"]["enable_thinking"] is False
    assert payload["temperature"] == 0.1
    assert "価格が高いと言われた" in payload["messages"][1]["content"]
