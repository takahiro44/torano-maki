"""検証済みの商談ナレッジJSONをDBへ投入する。

**なぜ必要か。**
`experiments/knowledge-extraction/` は「本番のDB・DDL・スキーマを変更しない」
という約束で作った検証であり、成果物はJSONで止まっている。
そのため**音声由来のナレッジをDBへ入れる経路が最初から存在しない。**

テキスト投入（`/ingest/text`）も `utterance_segments` を作るが、
あれは抽出根拠の抜粋から擬似的に生成したもので、話者は `"source"`、
時刻は `0.0`〜`0.01` の固定値しか持たない。
そのため Agent Tool の `get_utterance_context`（前後の発話を辿る）と
`get_call_summary` は、テキスト由来のデータでは実質的に機能しない。
**本物の発話・話者・タイムスタンプを持つのは音声由来のこのデータだけ**なので、
Agent Loop を動かすにはこの投入経路が要る。

LLMは使わない（抽出は2026-08-24に実行済み）。埋め込みだけを各自のPCのCPUで
生成するため、**DGXのvLLMが落ちていても実行できる。**

APIではなくDBへ直接書く。`utterance_segments` / `knowledge_evidence` /
`call_summaries` に対応するエンドポイントが無く、
1商談分を1トランザクションで入れたいため。

既定で `experiments/knowledge-extraction/output/meetings/` の全商談を
**1トランザクションで**投入する。`git pull` した人がこのコマンド1つで
チームと同じデータを手元に用意できる状態を保つこと。

使い方:
    cd backend
    uv run python scripts/load_extraction_json.py            # 全商談を投入
    uv run python scripts/load_extraction_json.py --replace  # 入れ直す
    uv run python scripts/load_extraction_json.py --file <path>   # 1件だけ
    uv run python scripts/load_extraction_json.py --status draft
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models.knowledge import KnowledgeStatus
from app.models.tables import (
    CallSummaryTable,
    DataSourceTable,
    KnowledgeEvidenceTable,
    KnowledgeUnitTable,
    UtteranceSegmentTable,
)
from app.services.embedding import embed_passages
from app.services.search_text import generate_search_text_from_mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXTRACTION_DIR = _REPO_ROOT / "experiments" / "knowledge-extraction" / "output"
# 商談1件につき1ファイル。既定でこのディレクトリを丸ごと入れる。
# 1件ずつ指定させると、22件を投入するのに22回コマンドを叩くことになり、
# そのたびに埋め込みモデル（2.2GB）の読み込みが走って10分近くかかる。
DEFAULT_DIR = _EXTRACTION_DIR / "meetings"
# 2026-08-24の検証結果。`meetings/01_order_entry.json` と**同じ商談**
# （どちらも tts-demo/scripts/01_order_entry.json の台本から合成した音声）なので、
# 両方入れると同じナレッジが検索結果に二重に出る。既定では読まない。
LEGACY_JSON = _EXTRACTION_DIR / "knowledge_extraction.json"


# 実験側の schema.py は別の uv プロジェクトにあり、backend から import できない。
# 構造がずれたら読み込み時に落ちるよう、ここで受け入れる形を明示しておく
# （dictのまま扱うとキーの取り違えが実行時まで分からないため）。
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataSourceIn(_Strict):
    """1商談の出典。

    **検証JSONと、DBから書き出したJSON（`export_audio_knowledge.py`）の
    両方を受ける。** 前者は id / source_type / file_name / occurred_at しか
    持たないため、それ以外は省略可能にしてある。

    `origin` を運べるようにしているのは、実商談として登録した音声を
    退避して戻したときに、合成商談として並んでしまうのを防ぐため
    （02_schema.sql の「合成データを実商談と誤認させないため」）。
    """

    id: UUID
    source_type: str
    # 画面からアップロードした音声は、日時が分からないまま登録されることがある
    file_name: str | None = None
    occurred_at: datetime | None = None
    # 省略時は投入コマンドの --origin に従う（検証JSONにはこの列が無い）
    origin: str | None = None
    review_status: str | None = None
    created_at: datetime | None = None


class UtteranceSegmentIn(_Strict):
    id: UUID
    data_source_id: UUID
    sequence_no: int
    speaker: str
    start_sec: float
    end_sec: float
    content: str


class KnowledgeUnitIn(_Strict):
    id: UUID
    data_source_id: UUID
    knowledge_type: str
    title: str
    situation: str | None
    problem: str | None
    judgment: str | None
    action: str | None
    reasoning: str | None
    outcome: str | None
    lesson: str | None
    applicable_situations: str | None
    limitations: str | None
    industry: str | None
    product: str | None
    sales_stage: str | None
    search_text: str
    embedding: list[float] | None
    embedding_model: str | None
    created_at: datetime


class KnowledgeEvidenceIn(_Strict):
    id: UUID
    knowledge_id: UUID
    start_utterance_id: UUID
    end_utterance_id: UUID


class CallSummaryIn(_Strict):
    id: UUID
    data_source_id: UUID
    summary: str
    customer_needs: list[str]
    proposals: list[str]
    decisions: list[str]
    next_actions: list[str]


class ExtractionResult(_Strict):
    """検証が出力する、ER図の5テーブルに対応した1商談分のデータ。"""

    data_sources: list[DataSourceIn]
    utterance_segments: list[UtteranceSegmentIn]
    knowledge_units: list[KnowledgeUnitIn]
    knowledge_evidence: list[KnowledgeEvidenceIn]
    call_summaries: list[CallSummaryIn]


def load_result(path: Path) -> ExtractionResult:
    return ExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))


def merge_results(paths: list[Path]) -> ExtractionResult:
    """複数商談のJSONを1つにまとめる。

    **1トランザクションで入れるため**にまとめている。ファイルごとに
    投入すると、途中で失敗したときに「何件目まで入ったか」が分からない
    中途半端なDBが残る。埋め込みも `insert_result` が一括で生成するので、
    まとめた方がモデルの読み込みが1回で済む。

    同じ商談が複数ファイルに現れたら止める。決定的UUIDなので、
    そのまま入れると主キーの衝突で落ちるが、原因が分かりにくい。
    """
    merged = ExtractionResult(
        data_sources=[],
        utterance_segments=[],
        knowledge_units=[],
        knowledge_evidence=[],
        call_summaries=[],
    )
    seen: dict[UUID, Path] = {}
    for path in paths:
        result = load_result(path)
        for source in result.data_sources:
            if source.id in seen:
                raise ValueError(
                    f"同じ商談が2つのファイルにあります: {seen[source.id].name} と {path.name}"
                    f"（{source.file_name}）"
                )
            seen[source.id] = path
        merged.data_sources.extend(result.data_sources)
        merged.utterance_segments.extend(result.utterance_segments)
        merged.knowledge_units.extend(result.knowledge_units)
        merged.knowledge_evidence.extend(result.knowledge_evidence)
        merged.call_summaries.extend(result.call_summaries)
    return merged


def source_ids(result: ExtractionResult) -> list[UUID]:
    return [s.id for s in result.data_sources]


def find_existing(db: Session, ids: list[UUID]) -> list[UUID]:
    """既に入っている data_source を返す。

    検証側のUUIDは決定的に生成されるため、同じJSONを2回入れると
    主キーの衝突で途中まで書かれた状態になる。事前に検出して止める。
    """
    if not ids:
        return []
    rows = db.execute(select(DataSourceTable.id).where(DataSourceTable.id.in_(ids))).scalars()
    return list(rows)


def delete_sources(db: Session, ids: list[UUID]) -> None:
    """指定した商談を、外部キーの順に**物理削除**する。

    ナレッジ本体の DELETE API は論理削除だが、ここは入れ直しが目的なので
    行を残すと決定的UUIDが再び衝突する。
    """
    if not ids:
        return
    knowledge_ids = list(
        db.execute(
            select(KnowledgeUnitTable.id).where(KnowledgeUnitTable.data_source_id.in_(ids))
        ).scalars()
    )
    db.execute(delete(CallSummaryTable).where(CallSummaryTable.data_source_id.in_(ids)))
    if knowledge_ids:
        db.execute(
            delete(KnowledgeEvidenceTable).where(
                KnowledgeEvidenceTable.knowledge_id.in_(knowledge_ids)
            )
        )
    db.execute(delete(KnowledgeUnitTable).where(KnowledgeUnitTable.data_source_id.in_(ids)))
    db.execute(delete(UtteranceSegmentTable).where(UtteranceSegmentTable.data_source_id.in_(ids)))
    db.execute(delete(DataSourceTable).where(DataSourceTable.id.in_(ids)))


def insert_result(
    db: Session,
    result: ExtractionResult,
    *,
    status: str,
    origin: str = "synthetic",
) -> dict[str, int]:
    """5テーブルを外部キーの順に挿入する。commitは呼び出し側が行う。

    `search_text` はJSONの値をそのまま使わず `services/search_text.py` で
    作り直す。語彙検索（pg_trgm）は `search_text` を直接見るため、
    投入経路ごとに書式が違うと同じクエリでも当たり方が変わってしまう。

    **`origin` は既定で `synthetic`。** この経路で入るのは
    `tts-demo/scripts/*.json` の台本からTTSで合成した商談だけであり、
    実際の顧客との商談ではない。`data_sources.origin` の既定は `real` なので、
    指定しないと**合成データが実商談として並ぶ**（02_schema.sql の
    「合成データを実商談と誤認させないため」を参照）。実データを入れる場合だけ
    `origin="real"` を明示すること。
    """
    for source in result.data_sources:
        # JSONが origin を持っていればそちらを優先する。退避したデータを
        # 戻すときに、実商談が合成として復元されると区別が付かなくなる。
        # None の列は落としてDBの既定（review_status / created_at）に任せる
        values = {k: v for k, v in source.model_dump().items() if v is not None}
        values.setdefault("origin", origin)
        db.add(DataSourceTable(**values))
    for segment in result.utterance_segments:
        db.add(UtteranceSegmentTable(**segment.model_dump()))
    db.flush()

    embedding_model = get_settings().embedding_model
    search_texts = [
        generate_search_text_from_mapping(unit.model_dump()) for unit in result.knowledge_units
    ]
    # 1件ずつ encode するとモデル呼び出しの固定コストが件数分かかるため、まとめて渡す
    vectors = embed_passages(search_texts)

    for unit, search_text, vector in zip(
        result.knowledge_units, search_texts, vectors, strict=True
    ):
        values = unit.model_dump()
        values["search_text"] = search_text
        values["embedding"] = vector
        values["embedding_model"] = embedding_model
        db.add(KnowledgeUnitTable(**values, status=status))
    db.flush()

    for evidence in result.knowledge_evidence:
        db.add(KnowledgeEvidenceTable(**evidence.model_dump()))
    for summary in result.call_summaries:
        db.add(CallSummaryTable(**summary.model_dump()))
    db.flush()

    return {
        "data_sources": len(result.data_sources),
        "utterance_segments": len(result.utterance_segments),
        "knowledge_units": len(result.knowledge_units),
        "knowledge_evidence": len(result.knowledge_evidence),
        "call_summaries": len(result.call_summaries),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_DIR,
        help=f"この中の *.json をすべて投入する（既定: {DEFAULT_DIR}）",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="1件だけ投入する。指定すると --dir は無視される",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="同じ商談が既にある場合、削除してから入れ直す（物理削除）",
    )
    parser.add_argument(
        "--status",
        default=KnowledgeStatus.CONFIRMED.value,
        choices=[s.value for s in KnowledgeStatus],
        help="投入するナレッジの状態（既定: confirmed。draft は検索に出ない）",
    )
    parser.add_argument(
        "--origin",
        default="synthetic",
        choices=["synthetic", "real"],
        help="商談の出所（既定: synthetic。この経路で入るのはTTSで合成した商談のため）",
    )
    args = parser.parse_args()

    if args.file is not None:
        if not args.file.exists():
            print(f"ファイルがありません: {args.file}", file=sys.stderr)
            return 1
        paths = [args.file]
    else:
        if not args.dir.is_dir():
            print(f"ディレクトリがありません: {args.dir}", file=sys.stderr)
            return 1
        paths = sorted(args.dir.glob("*.json"))
        if not paths:
            print(f"投入するJSONがありません: {args.dir}", file=sys.stderr)
            return 1

    try:
        result = merge_results(paths)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    ids = source_ids(result)
    print(f"入力: {len(paths)}ファイル / 商談{len(ids)}件")

    db = SessionLocal()
    try:
        existing = find_existing(db, ids)
        if existing and not args.replace:
            print(
                f"同じ商談が既に入っています（{len(existing)}件）。\n"
                "入れ直す場合は --replace を付けてください。",
                file=sys.stderr,
            )
            return 1
        if existing:
            try:
                delete_sources(db, existing)
            except IntegrityError as error:
                # ロープレやチャットレビューが、消そうとしているナレッジを
                # 参照していると外部キーで落ちる。素の IntegrityError は
                # SQLとUUIDの羅列で、原因も対処も読み取れない。
                # ここは他メンバーの担当領域なので**勝手に消さず**、
                # 何が掴んでいるかだけを伝えて止める。
                db.rollback()
                print(
                    "既存の商談を削除できません。ロープレ等のデータが"
                    "ナレッジを参照しています。\n"
                    "参照している側を先に消すか、DBを作り直してください:\n"
                    "  docker compose down -v && docker compose up -d\n"
                    f"詳細: {error.orig}",
                    file=sys.stderr,
                )
                return 1
            print(f"既存の商談 {len(existing)}件を削除しました")

        print("埋め込みを生成しています（初回はモデル読み込みで数十秒かかります）…")
        counts = insert_result(db, result, status=args.status, origin=args.origin)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"\n投入しました（status={args.status} / origin={args.origin}）")
    for name, count in counts.items():
        print(f"  {name:<20} {count:>4}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
