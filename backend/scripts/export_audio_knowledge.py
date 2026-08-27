"""登録済み音声に紐づくナレッジを、班員が復元できるJSONへ書き出す。

音声ファイル自体は保存・出力しない。DBに残っている出典、時刻付き文字起こし、
ナレッジ、根拠、商談要約だけを書き出す。埋め込みはPCやモデル設定に依存するため
含めず、復元側の ``load_extraction_json.py`` が再生成する。

使い方（既定では最新の音声に紐づく confirmed ナレッジを出力）:
    cd backend
    uv run python scripts/export_audio_knowledge.py
    uv run python scripts/export_audio_knowledge.py --file-name meeting.wav
    uv run python scripts/export_audio_knowledge.py --source-id <UUID> --output ../data/demo.json

復元:
    cd backend
    uv run python scripts/load_extraction_json.py --file ../data/audio_knowledge_export.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.knowledge import KnowledgeStatus
from app.models.tables import (
    CallSummaryTable,
    DataSourceTable,
    KnowledgeEvidenceTable,
    KnowledgeUnitTable,
    UtteranceSegmentTable,
)
from app.services.search_text import generate_search_text_from_mapping
from scripts.load_extraction_json import (
    CallSummaryIn,
    DataSourceIn,
    ExtractionResult,
    KnowledgeEvidenceIn,
    KnowledgeUnitIn,
    UtteranceSegmentIn,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = _REPO_ROOT / "data" / "audio_knowledge_export.json"


def find_audio_source(
    db: Session,
    *,
    source_id: UUID | None = None,
    file_name: str | None = None,
) -> DataSourceTable | None:
    """明示指定、ファイル名、最新音声の順で対象を一意に決める。"""
    query = select(DataSourceTable).where(DataSourceTable.source_type == "audio")
    if source_id is not None:
        query = query.where(DataSourceTable.id == source_id)
    elif file_name is not None:
        query = query.where(DataSourceTable.file_name == file_name)
    query = query.order_by(DataSourceTable.created_at.desc(), DataSourceTable.id.desc()).limit(1)
    return db.execute(query).scalar_one_or_none()


def build_export_result(
    db: Session,
    source: DataSourceTable,
    *,
    knowledge_status: str = KnowledgeStatus.CONFIRMED.value,
) -> ExtractionResult:
    """1音声分の関連行を、既存の復元スクリプトが読める形にまとめる。"""
    segments = list(
        db.execute(
            select(UtteranceSegmentTable)
            .where(UtteranceSegmentTable.data_source_id == source.id)
            .order_by(UtteranceSegmentTable.sequence_no.asc())
        )
        .scalars()
        .all()
    )
    units = list(
        db.execute(
            select(KnowledgeUnitTable)
            .where(
                KnowledgeUnitTable.data_source_id == source.id,
                KnowledgeUnitTable.status == knowledge_status,
                KnowledgeUnitTable.deleted_at.is_(None),
            )
            .order_by(KnowledgeUnitTable.created_at.asc(), KnowledgeUnitTable.id.asc())
        )
        .scalars()
        .all()
    )
    if not units:
        raise ValueError(
            f"{source.file_name or source.id} に status={knowledge_status} のナレッジがありません。"
            "画面でナレッジ化・承認してから再実行してください。"
        )

    knowledge_ids = [unit.id for unit in units]
    evidence = list(
        db.execute(
            select(KnowledgeEvidenceTable)
            .where(KnowledgeEvidenceTable.knowledge_id.in_(knowledge_ids))
            .order_by(KnowledgeEvidenceTable.created_at.asc(), KnowledgeEvidenceTable.id.asc())
        )
        .scalars()
        .all()
    )
    summaries = list(
        db.execute(select(CallSummaryTable).where(CallSummaryTable.data_source_id == source.id))
        .scalars()
        .all()
    )

    return ExtractionResult(
        data_sources=[
            DataSourceIn(
                id=source.id,
                source_type=source.source_type,
                file_name=source.file_name,
                occurred_at=source.occurred_at,
                origin=source.origin,
                review_status=source.review_status,
                created_at=source.created_at,
            )
        ],
        utterance_segments=[
            UtteranceSegmentIn(
                id=row.id,
                data_source_id=row.data_source_id,
                sequence_no=row.sequence_no,
                speaker=row.speaker,
                start_sec=row.start_sec,
                end_sec=row.end_sec,
                content=row.content,
            )
            for row in segments
        ],
        knowledge_units=[_knowledge_unit_in(row) for row in units],
        knowledge_evidence=[
            KnowledgeEvidenceIn(
                id=row.id,
                knowledge_id=row.knowledge_id,
                start_utterance_id=row.start_utterance_id,
                end_utterance_id=row.end_utterance_id,
            )
            for row in evidence
        ],
        call_summaries=[
            CallSummaryIn(
                id=row.id,
                data_source_id=row.data_source_id,
                summary=row.summary,
                customer_needs=row.customer_needs,
                proposals=row.proposals,
                decisions=row.decisions,
                next_actions=row.next_actions,
            )
            for row in summaries
        ],
    )


def _knowledge_unit_in(row: KnowledgeUnitTable) -> KnowledgeUnitIn:
    values = {
        "title": row.title,
        "situation": row.situation,
        "problem": row.problem,
        "judgment": row.judgment,
        "action": row.action,
        "reasoning": row.reasoning,
        "outcome": row.outcome,
        "lesson": row.lesson,
        "applicable_situations": row.applicable_situations,
        "limitations": row.limitations,
        "industry": row.industry,
        "product": row.product,
        "sales_stage": row.sales_stage,
    }
    return KnowledgeUnitIn(
        id=row.id,
        data_source_id=row.data_source_id,
        knowledge_type=row.knowledge_type,
        **values,
        search_text=row.search_text or generate_search_text_from_mapping(values),
        # 復元先の設定に合わせて再生成するので、サイズの大きいベクトルは運ばない。
        embedding=None,
        embedding_model=None,
        created_at=row.created_at,
    )


def write_result(result: ExtractionResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump(mode="json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--source-id", type=UUID, help="文字起こしAPIが返した data_source_id")
    selector.add_argument("--file-name", help="アップロード時のファイル名（同名なら最新）")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"出力先（既定: {DEFAULT_OUTPUT}。リポジトリの /data はgit管理外）",
    )
    parser.add_argument(
        "--status",
        default=KnowledgeStatus.CONFIRMED.value,
        choices=[status.value for status in KnowledgeStatus],
        help="書き出すナレッジの状態（既定: confirmed）",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        source = find_audio_source(db, source_id=args.source_id, file_name=args.file_name)
        if source is None:
            print(
                "対象の音声がDBにありません。先に画面からアップロードしてください。",
                file=sys.stderr,
            )
            return 1
        try:
            result = build_export_result(db, source, knowledge_status=args.status)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        write_result(result, args.output)
    finally:
        db.close()

    print(f"書き出しました: {args.output.resolve()}")
    print(f"  data_source_id       {source.id}")
    print(f"  file_name            {source.file_name}")
    print(f"  utterance_segments   {len(result.utterance_segments)}件")
    print(f"  knowledge_units      {len(result.knowledge_units)}件（status={args.status}）")
    print(f"  knowledge_evidence   {len(result.knowledge_evidence)}件")
    print(f"  call_summaries       {len(result.call_summaries)}件")
    print("\nこのJSONには文字起こし全文が含まれます。共有先を確認してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
