"""検証済みJSONの投入スクリプトのテスト。

コミット済みのJSON（`experiments/knowledge-extraction/output/`）を実データとして
そのまま使う。検証側の出力構造が変わったら、ここが最初に落ちるようにしておくため。

埋め込みの生成は差し替える。モデルの読み込みに20秒以上かかるうえ、
ベクトルの正しさは `test_embedding.py` で見ており、
このテストで確かめたいのは**外部キーが繋がった状態で5テーブルに入ること**だから。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.tables import (
    CallSummaryTable,
    DataSourceTable,
    KnowledgeEvidenceTable,
    KnowledgeUnitTable,
    UtteranceSegmentTable,
)
from scripts.load_extraction_json import (
    DEFAULT_JSON,
    ExtractionResult,
    delete_sources,
    find_existing,
    insert_result,
    load_result,
    source_ids,
)


@pytest.fixture(scope="module")
def result() -> ExtractionResult:
    if not DEFAULT_JSON.exists():
        pytest.skip(f"検証済みJSONがありません: {DEFAULT_JSON}")
    return load_result(DEFAULT_JSON)


@pytest.fixture
def fake_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    dim = get_settings().embedding_dim

    def _embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * dim for _ in texts]

    monkeypatch.setattr("scripts.load_extraction_json.embed_passages", _embed)


@pytest.fixture
def clean_slate(db: Session, result: ExtractionResult) -> None:
    """同じ商談が既にDBに入っていても通るようにする。

    このスクリプトは決定的UUIDを使うため、**実際に投入済みの環境では
    主キーが衝突してテストが落ちる。** 削除は `db` フィクスチャの
    トランザクション内なので、テスト後にロールバックされ実データは消えない。
    """
    delete_sources(db, source_ids(result))
    db.flush()


def test_json_matches_expected_shape(result: ExtractionResult) -> None:
    """検証側の出力が5テーブル分そろっていること。"""
    assert len(result.data_sources) == 1
    assert result.data_sources[0].source_type == "audio"
    assert result.utterance_segments, "発話が空"
    assert result.knowledge_units, "ナレッジが空"
    assert result.knowledge_evidence, "根拠が空"
    assert len(result.call_summaries) == 1


def test_evidence_points_to_existing_utterances(result: ExtractionResult) -> None:
    """根拠が実在する発話を指していること。

    ここがずれたまま投入すると外部キー違反になる。
    投入前にJSON単体で分かるようにしておく。
    """
    utterance_ids = {u.id for u in result.utterance_segments}
    knowledge_ids = {k.id for k in result.knowledge_units}
    for evidence in result.knowledge_evidence:
        assert evidence.knowledge_id in knowledge_ids
        assert evidence.start_utterance_id in utterance_ids
        assert evidence.end_utterance_id in utterance_ids


def test_insert_stores_all_tables(
    db: Session, result: ExtractionResult, fake_embedding: None, clean_slate: None
) -> None:
    counts = insert_result(db, result, status="confirmed")

    source_id = result.data_sources[0].id
    assert db.get(DataSourceTable, source_id) is not None

    segments = db.execute(
        select(func.count())
        .select_from(UtteranceSegmentTable)
        .where(UtteranceSegmentTable.data_source_id == source_id)
    ).scalar_one()
    assert segments == counts["utterance_segments"]

    units = list(
        db.execute(
            select(KnowledgeUnitTable).where(KnowledgeUnitTable.data_source_id == source_id)
        ).scalars()
    )
    assert len(units) == counts["knowledge_units"]
    assert all(u.status == "confirmed" for u in units)
    assert all(u.embedding is not None for u in units), "埋め込みが無いと検索に出ない"
    assert all(u.embedding_model == get_settings().embedding_model for u in units)

    summary = db.execute(
        select(CallSummaryTable).where(CallSummaryTable.data_source_id == source_id)
    ).scalar_one()
    assert summary.summary
    assert summary.next_actions


def test_search_text_is_regenerated(
    db: Session, result: ExtractionResult, fake_embedding: None, clean_slate: None
) -> None:
    """JSONの search_text ではなく services/search_text.py の書式で入ること。

    語彙検索（pg_trgm）は search_text を直接見るため、投入経路ごとに
    書式が違うと同じクエリでも当たり方が変わる。
    """
    insert_result(db, result, status="confirmed")

    row = db.get(KnowledgeUnitTable, result.knowledge_units[0].id)
    assert row is not None
    assert row.search_text is not None
    assert "状況: " in row.search_text
    assert "\n" not in row.search_text


def test_replace_removes_previous_rows(
    db: Session, result: ExtractionResult, fake_embedding: None, clean_slate: None
) -> None:
    """同じJSONを入れ直せること。

    検証側のUUIDは決定的に生成されるため、削除せずに再投入すると
    主キーが衝突する。--replace の経路を確かめる。
    """
    insert_result(db, result, status="confirmed")
    db.flush()

    ids = source_ids(result)
    assert find_existing(db, ids) == ids

    delete_sources(db, ids)
    db.flush()

    assert find_existing(db, ids) == []
    remaining = db.execute(
        select(func.count())
        .select_from(KnowledgeEvidenceTable)
        .join(KnowledgeUnitTable, KnowledgeEvidenceTable.knowledge_id == KnowledgeUnitTable.id)
        .where(KnowledgeUnitTable.data_source_id.in_(ids))
    ).scalar_one()
    assert remaining == 0

    insert_result(db, result, status="confirmed")
    assert find_existing(db, ids) == ids
