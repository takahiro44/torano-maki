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
    DEFAULT_DIR,
    LEGACY_JSON,
    ExtractionResult,
    delete_sources,
    find_existing,
    insert_result,
    load_result,
    merge_results,
    source_ids,
)


@pytest.fixture(scope="module")
def result() -> ExtractionResult:
    """1商談ぶんの実データ。

    複数商談をまとめる経路は `test_merge_*` で見る。ここは
    「1商談が5テーブルに正しく入るか」を確かめる場所なので、
    件数が固定のファイルを使う方が assert を具体的に書ける。
    """
    if not LEGACY_JSON.exists():
        pytest.skip(f"検証済みJSONがありません: {LEGACY_JSON}")
    return load_result(LEGACY_JSON)


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


def test_merge_reads_every_meeting_in_the_directory() -> None:
    """既定のディレクトリを丸ごと読めること。

    チームは `git pull` 後に引数なしでこのスクリプトを実行する。
    ここが黙って一部しか読まなくなると、**人によってDBの中身が違う**
    状態になり、検索結果の差がデータ由来なのかコード由来なのか分からなくなる。
    """
    if not DEFAULT_DIR.is_dir():
        pytest.skip(f"商談JSONがありません: {DEFAULT_DIR}")
    paths = sorted(DEFAULT_DIR.glob("*.json"))
    merged = merge_results(paths)

    assert len(merged.data_sources) == len(paths), "ファイル数と商談数が一致しない"
    assert len({s.id for s in merged.data_sources}) == len(paths), "商談IDが重複している"

    # 外部キーが商談をまたいで壊れていないこと
    utterance_ids = {u.id for u in merged.utterance_segments}
    source_ids_set = {s.id for s in merged.data_sources}
    assert {u.data_source_id for u in merged.utterance_segments} <= source_ids_set
    assert {k.data_source_id for k in merged.knowledge_units} <= source_ids_set
    for evidence in merged.knowledge_evidence:
        assert evidence.start_utterance_id in utterance_ids
        assert evidence.end_utterance_id in utterance_ids


def test_merge_rejects_the_same_meeting_twice() -> None:
    """同じ商談を2回渡したら、投入前に止まること。

    UUIDは決定的なので、通すと主キー衝突で落ちる。DBまで行くと
    「途中まで入った」状態になり、原因が分かりにくい。
    """
    if not DEFAULT_DIR.is_dir():
        pytest.skip(f"商談JSONがありません: {DEFAULT_DIR}")
    path = sorted(DEFAULT_DIR.glob("*.json"))[0]
    with pytest.raises(ValueError, match="同じ商談"):
        merge_results([path, path])


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
