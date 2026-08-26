"""Knowledge に紐づく根拠・文脈を、IDと外部キーだけで安全に取得する。

**なぜ切り出すか。**
同じ「Evidence から発話範囲を引く」処理が `agent_tools.py`（AIチャット）に
あり、ロープレでも同じものが要る。private 関数を跨いで import すると、
片方の都合で壊れたときにもう片方が静かに壊れる。整合性チェック
（KnowledgeとEvidenceの出典が一致するか等）を2箇所で書き分けるのは
特に危険なので、検証ごとここへ集約する（計画書5章）。

**Semantic Search は行わない。** 一次検索は `services/search.py` の
責務で、このモジュールは「既に選ばれた Knowledge の周辺を引く」だけを行う。
入口を分けておかないと、根拠取得のつもりで別のナレッジを拾ってくる事故が起きる。

返すのは dict ではなく dataclass。利用側が `row["utterances"][0]["speaker"]`
のような文字列キーで触ると、列名を変えたときに実行時まで気づけないため。
JSON 化は利用側（Tool 応答など）が行う。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeStatus
from app.models.tables import (
    CallSummaryTable,
    KnowledgeEvidenceTable,
    KnowledgeUnitTable,
    UtteranceSegmentTable,
)

# 1つの Evidence 範囲から取り出す発話の上限。
#
# 抽出のチャンク境界によっては、Evidence が商談のほぼ全体を指すことがある。
# そのままプロンプトへ載せると、シナリオ生成が「どの場面の話か」を見失い、
# 待ち時間も伸びる。頭から切るのは、練習したいのは分岐点の入り口だから。
MAX_UTTERANCES_PER_SPAN = 40


class KnowledgeContextError(RuntimeError):
    """想定内の参照エラー。

    `code` を持たせているのは、呼び出し側が HTTP ステータスや
    Agent への応答へ機械的に変換できるようにするため。
    文言だけだと分岐条件が日本語の部分一致になり、壊れやすい。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ContextUtterance:
    """1発話。`is_evidence` で根拠そのものと周辺文脈を区別する。"""

    id: UUID
    data_source_id: UUID
    sequence_no: int
    speaker: str
    start_sec: float
    end_sec: float
    content: str
    is_evidence: bool = True


@dataclass(frozen=True)
class EvidenceSpan:
    """Knowledge の根拠となった発話範囲。"""

    evidence_id: UUID
    data_source_id: UUID
    start_utterance_id: UUID
    end_utterance_id: UUID
    start_sequence_no: int
    end_sequence_no: int
    utterances: list[ContextUtterance]
    truncated: bool = False


@dataclass(frozen=True)
class UtteranceWindow:
    """ある発話・範囲の前後を含めた並び。"""

    data_source_id: UUID
    evidence_start_sequence_no: int
    evidence_end_sequence_no: int
    context_start_sequence_no: int
    context_end_sequence_no: int
    utterances: list[ContextUtterance]


@dataclass(frozen=True)
class KnowledgeContext:
    """1つの Knowledge と、その根拠・背景をまとめたもの。

    ロープレのシナリオ生成に必要な材料はこれで揃う。
    利用側が Evidence と Summary を別々に引いて組み合わせると、
    「出典が違うものを混ぜる」事故が起きるため、まとめて返す。
    """

    knowledge: KnowledgeUnitTable
    spans: list[EvidenceSpan]
    summary: CallSummaryTable | None = None

    @property
    def has_evidence(self) -> bool:
        return any(span.utterances for span in self.spans)


def _to_context_utterance(
    row: UtteranceSegmentTable, *, is_evidence: bool = True
) -> ContextUtterance:
    return ContextUtterance(
        id=row.id,
        data_source_id=row.data_source_id,
        sequence_no=row.sequence_no,
        speaker=row.speaker,
        start_sec=row.start_sec,
        end_sec=row.end_sec,
        content=row.content,
        is_evidence=is_evidence,
    )


def _fetch_range(
    db: Session, data_source_id: UUID, start_seq: int, end_seq: int
) -> list[UtteranceSegmentTable]:
    return list(
        db.execute(
            select(UtteranceSegmentTable)
            .where(
                UtteranceSegmentTable.data_source_id == data_source_id,
                UtteranceSegmentTable.sequence_no >= start_seq,
                UtteranceSegmentTable.sequence_no <= end_seq,
            )
            .order_by(UtteranceSegmentTable.sequence_no)
        )
        .scalars()
        .all()
    )


def get_confirmed_knowledge(db: Session, knowledge_id: UUID) -> KnowledgeUnitTable:
    """確認済みの Knowledge を1件取る。

    draft を混ぜないのは、人が検証していない抽出結果を根拠として
    提示してしまうため（`services/search.py` の `_base_query` と同じ方針）。
    """
    row = db.execute(
        select(KnowledgeUnitTable).where(
            KnowledgeUnitTable.id == knowledge_id,
            KnowledgeUnitTable.deleted_at.is_(None),
            KnowledgeUnitTable.status == KnowledgeStatus.CONFIRMED,
        )
    ).scalar_one_or_none()
    if row is None:
        raise KnowledgeContextError("knowledge_not_found", "確認済みKnowledgeが見つかりません")
    return row


def get_evidence_spans(
    db: Session,
    knowledge: KnowledgeUnitTable,
    *,
    before: int = 0,
    after: int = 0,
    max_utterances: int = MAX_UTTERANCES_PER_SPAN,
) -> list[EvidenceSpan]:
    """Knowledge の根拠範囲を取る。

    `before` / `after` は範囲の前後に足す発話数。既定を0にしているのは、
    AIチャットの Tool が「根拠そのもの」だけを返す前提で作られており、
    黙って周辺を混ぜると Agent が根拠の境界を見誤るため。
    周辺文脈が要る場合（ロープレの出題）は呼び出し側が明示的に指定する。

    **整合性が壊れている Evidence はここで落とす。** 出典の異なる発話や
    開始・終了が逆転した範囲をそのまま使うと、無関係な商談の発言を
    根拠として提示することになる。
    """
    evidence_rows = list(
        db.execute(
            select(KnowledgeEvidenceTable)
            .where(KnowledgeEvidenceTable.knowledge_id == knowledge.id)
            .order_by(KnowledgeEvidenceTable.created_at, KnowledgeEvidenceTable.id)
        )
        .scalars()
        .all()
    )

    spans: list[EvidenceSpan] = []
    for evidence in evidence_rows:
        start = db.get(UtteranceSegmentTable, evidence.start_utterance_id)
        end = db.get(UtteranceSegmentTable, evidence.end_utterance_id)
        if start is None or end is None:
            raise KnowledgeContextError(
                "invalid_evidence", "Evidenceが存在しない発言を参照しています"
            )
        if start.data_source_id != end.data_source_id:
            raise KnowledgeContextError(
                "invalid_evidence", "Evidenceの開始・終了発言の出典が異なります"
            )
        if knowledge.data_source_id != start.data_source_id:
            raise KnowledgeContextError("invalid_evidence", "KnowledgeとEvidenceの出典が異なります")
        if start.sequence_no > end.sequence_no:
            raise KnowledgeContextError(
                "invalid_evidence", "Evidenceの開始発言が終了発言より後です"
            )

        context_start = max(1, start.sequence_no - before)
        context_end = end.sequence_no + after
        rows = _fetch_range(db, start.data_source_id, context_start, context_end)
        truncated = len(rows) > max_utterances
        rows = rows[:max_utterances]

        spans.append(
            EvidenceSpan(
                evidence_id=evidence.id,
                data_source_id=start.data_source_id,
                start_utterance_id=start.id,
                end_utterance_id=end.id,
                start_sequence_no=start.sequence_no,
                end_sequence_no=end.sequence_no,
                utterances=[
                    _to_context_utterance(
                        row,
                        is_evidence=start.sequence_no <= row.sequence_no <= end.sequence_no,
                    )
                    for row in rows
                ],
                truncated=truncated,
            )
        )
    return spans


def get_utterance_window(
    db: Session,
    start_utterance_id: UUID,
    end_utterance_id: UUID | None = None,
    *,
    before: int = 2,
    after: int = 2,
) -> UtteranceWindow:
    """1発話または範囲の前後を含めて取る。"""
    start = db.get(UtteranceSegmentTable, start_utterance_id)
    if start is None:
        raise KnowledgeContextError("utterance_not_found", "開始発言が見つかりません")

    end = db.get(UtteranceSegmentTable, end_utterance_id or start_utterance_id)
    if end is None:
        raise KnowledgeContextError("utterance_not_found", "終了発言が見つかりません")
    if start.data_source_id != end.data_source_id:
        raise KnowledgeContextError("invalid_utterance_range", "開始・終了発言の出典が異なります")
    if start.sequence_no > end.sequence_no:
        raise KnowledgeContextError("invalid_utterance_range", "開始発言が終了発言より後です")

    context_start = max(1, start.sequence_no - before)
    context_end = end.sequence_no + after
    rows = _fetch_range(db, start.data_source_id, context_start, context_end)

    return UtteranceWindow(
        data_source_id=start.data_source_id,
        evidence_start_sequence_no=start.sequence_no,
        evidence_end_sequence_no=end.sequence_no,
        context_start_sequence_no=context_start,
        context_end_sequence_no=context_end,
        utterances=[
            _to_context_utterance(
                row,
                is_evidence=start.sequence_no <= row.sequence_no <= end.sequence_no,
            )
            for row in rows
        ],
    )


def get_call_summary(db: Session, data_source_id: UUID) -> CallSummaryTable | None:
    """同じ入力元の商談要約。無ければ None。

    要約が無いことは異常ではない（要約生成は任意の操作）ため、
    例外にせず None を返す。
    """
    return db.execute(
        select(CallSummaryTable).where(CallSummaryTable.data_source_id == data_source_id)
    ).scalar_one_or_none()


def build_knowledge_context(
    db: Session,
    knowledge_id: UUID,
    *,
    before: int = 3,
    after: int = 2,
    include_summary: bool = False,
) -> KnowledgeContext:
    """Knowledge と根拠・背景をまとめて取る。ロープレの出題材料。

    `before` の既定を3にしているのは、根拠の直前を見ないと
    「今どんな商談場面か」が分からず、顧客の最初の発言を
    その場面に合ったものにできないため（計画書6章）。
    """
    knowledge = get_confirmed_knowledge(db, knowledge_id)
    spans = get_evidence_spans(db, knowledge, before=before, after=after)

    summary = None
    if include_summary and knowledge.data_source_id is not None:
        summary = get_call_summary(db, knowledge.data_source_id)

    return KnowledgeContext(knowledge=knowledge, spans=spans, summary=summary)
