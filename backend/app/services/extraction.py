"""LLMによる CBR 構造化ナレッジの抽出。"""

from __future__ import annotations

import json
import logging
import re
from bisect import bisect_right
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import (
    CBR_FIELD_LABELS,
    ExtractedKnowledge,
    ExtractionResult,
    KnowledgeStatus,
)
from app.models.tables import (
    DataSourceTable,
    KnowledgeEvidenceTable,
    KnowledgeUnitTable,
    UtteranceSegmentTable,
)
from app.services.embedding import generate_embedding
from app.services.search_text import generate_search_text_from_mapping

logger = logging.getLogger(__name__)

_SCHEMA_NAME = "knowledge_extraction"

_SYSTEM_PROMPT = """あなたは経験・ノウハウの抽出の専門家です。
入力テキストから、後で誰かの役に立つ具体的な経験・ノウハウ・情報を構造化して抽出してください。
営業に関する内容に限りません。教科書的な一般論や、入力にないきれいな言い換えは不要です。

## 抽出フォーマット

1件につき以下を抽出：

- title: 見出し（30文字以内）
- situation: 状況（何が起きたか）
- problem: 顧客課題（顧客側の障壁・懸念。該当しなければ null）
- judgment: 判断（何を考え、どう判断したか。該当しなければ null）
- action: 行動（具体的に何をしたか）
- reasoning: 理由（なぜその判断・行動を選んだか）
- outcome: 結果（どうなったか）
- lesson: 学び（後で使える教訓。次に同じ場面が来たら何をするか）
- applicable_situations: 適用場面（どんな場面で使えるか）
- limitations: 制約・非適用場面（使えない場面や注意点）
- industry: 業界（該当する場合のみ）
- product: 商材（該当する場合のみ）
- sales_stage: 商談フェーズ（初回/提案/クロージング等、該当する場合のみ）
- knowledge_type: 分類。"business"（営業・商談に関するノウハウ）
  または "casual"（営業に直接関係しないが役立つ情報・豆知識）のどちらかを必ず選ぶ

## 抽出する／しない

抽出するもの（営業か否かを問わない）:
- 特定の状況に対して取った具体的な行動、選んだ理由、結果
- 次に同じ場面が来たらどうすべきかの示唆
- 営業に直接関係しない実用的な情報（例: 「水道橋は外回りの合間に入れる中華屋が多い」
  「A駅前の駐車場は17時以降が安い」など、再利用価値のある具体的な情報）

抽出しないもの（完全なノイズのみ）:
- 具体的な情報・示唆が何も無い相槌や挨拶だけの文（例:「お疲れ様です」「頑張ります」のみ）
- 個人の感情表現だけで、行動や情報を伴わないもの
- 迷ったら抽出する側に倒してよい。「営業に関係ないから」という理由だけでは捨てない。
  捨ててよいのは、具体的な行動・情報が本当に何も無い場合だけ
- ただし短い走り書きでも、固有名詞・状況・リスクや次にやるべきことが書かれている場合は抽出する
  （例: 担当交代で引き継がないと更新が止まる、など）
- 根拠のない一般論だけを無理に1件にするより、空配列の方がよい

## 必須ルール（具体性）

- 入力にある固有名詞（会社名、人名、地名、店名、役職、製品名）と数値（金額、人数、期間、時刻）は、
  situation / problem / action / outcome / lesson のいずれかに必ず残す
- 抽象的な言い換えをせず、実際に言った・やった表現を優先する
- 「〜という考え方もある」「〜が望ましい」「〜することが有効である」のような弱い表現は避け、
  何をしたか・何が起きたかを書く
- 情報不足の項目は null（一般論で埋めない）。casual分類の場合は
  situation / problem / judgment / action / reasoning / outcome が全て null でもよい
  （title と lesson または situation の どちらかに要点が書かれていれば十分）
- 1テキストから複数件抽出可。抽出できなければ空配列
- 各項目は1〜3文で簡潔に
- テキストに書かれた事実に基づく（推測しない）
- title は「〜の対応法」「〜への切り返し」のような検索しやすい表現
- 出力は指定の JSON Schema に厳密に従う。説明文やコードフェンスは付けない

## 抽出例

悪い例（一般論・抽象）:
- lesson: 「顧客の話をよく聞くことが大切」
- action: 「適切な提案を行った」
- title: 「提案のコツ」

良い例（business）:
- lesson: 「価格を指摘されたら値引きの前に比較軸を聞く。
  今回は保守対応の質が争点で、24時間対応と現地エンジニア常駐を説明して受注した」
- action: 「営業部30名だけの段階導入を出し、初年度を予算内に収める提案をした」
- title: 「価格指摘時の比較軸確認と価値訴求の対応法」
- knowledge_type: "business"

良い例（casual）:
- title: 「水道橋のランチ情報」
- lesson: 「水道橋の外回りの合間には、量が多くて提供が早い中華屋がある」
- situation / problem / judgment / action / reasoning / outcome: null
- knowledge_type: "casual"
"""

_CHUNK_CHARS = 4000
_CHUNK_OVERLAP = 250
_MAX_CHUNKS = 8
# DGXは4人で共有しており、他メンバーの利用状況次第で1チャンク90秒を
# 超えることが実測で確認された（chat.py が180秒以上を推奨しているのと同じ理由）。
# フロント側（api/client.ts の ingestText）は720秒まで待つ設計なので、
# ここを詰まらせているのはこの値だった。
_EXTRACT_TIMEOUT = 180.0


class LlmNotConfiguredError(RuntimeError):
    """BASE_URL / MODEL_NAME が空のときに上げる。"""


class LlmRequestError(RuntimeError):
    """vLLM への到達に失敗したときに上げる。"""


def extraction_json_schema() -> dict[str, Any]:
    return ExtractionResult.model_json_schema()


def build_extraction_payload(text: str, *, model: str) -> dict[str, Any]:
    schema = extraction_json_schema()
    schema_text = json.dumps(schema, ensure_ascii=False)
    user_content = (
        "次の JSON Schema に従って、ナレッジ配列を JSON だけ出力してください。\n"
        "一般論ではなく、入力に書かれた具体的な行動・固有名詞・数値を残してください。\n"
        f"{schema_text}\n\n"
        "以下のテキストからナレッジを抽出してください:\n\n"
        f"{text}"
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": _SCHEMA_NAME, "schema": schema},
        },
        "structured_outputs": {"json": schema},
        "chat_template_kwargs": {"enable_thinking": False},
    }


def format_item_as_content(item: ExtractedKnowledge) -> str:
    blocks: list[str] = []
    for field_name, label in CBR_FIELD_LABELS:
        value = getattr(item, field_name, None)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        blocks.append(f"【{label}】\n{text}")
    return "\n\n".join(blocks)


def split_source_text(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    if len(normalized) <= _CHUNK_CHARS:
        return [normalized]

    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length and len(chunks) < _MAX_CHUNKS:
        end = min(start + _CHUNK_CHARS, length)
        if end < length:
            window = normalized[start:end]
            break_at = max(window.rfind("\n\n"), window.rfind("。"), window.rfind("\n"))
            if break_at >= _CHUNK_CHARS // 2:
                end = start + break_at + 1
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        nxt = end - _CHUNK_OVERLAP
        start = nxt if nxt > start else end
    return chunks


def source_was_truncated(text: str) -> bool:
    n = len(text.replace("\r\n", "\n").strip())
    capacity = _CHUNK_CHARS * _MAX_CHUNKS - _CHUNK_OVERLAP * max(_MAX_CHUNKS - 1, 0)
    return n > capacity


def extract_knowledge_with_sources(text: str) -> list[tuple[ExtractedKnowledge, str]]:
    settings = get_settings()
    if not settings.is_llm_configured:
        raise LlmNotConfiguredError("BASE_URL / MODEL_NAME が未設定。.env を確認すること")

    chunks = split_source_text(text)
    if not chunks:
        return []

    collected: list[tuple[ExtractedKnowledge, str]] = []
    seen_titles: set[str] = set()
    url = settings.base_url.rstrip("/") + "/chat/completions"
    for chunk in chunks:
        items = _extract_one_chunk(chunk, model=settings.model_name, url=url)
        for item in items:
            key = item.title.strip()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            collected.append((item, chunk))
    return collected


def extract_knowledge(text: str) -> ExtractionResult:
    """テキストから構造化ナレッジを抽出する。パース失敗時は空結果。"""
    try:
        items = [item for item, _ in extract_knowledge_with_sources(text)]
    except LlmNotConfiguredError:
        raise
    except LlmRequestError:
        raise
    return ExtractionResult(items=items)


def _extract_one_chunk(text: str, *, model: str, url: str) -> list[ExtractedKnowledge]:
    payload = build_extraction_payload(text, model=model)
    try:
        with httpx.Client(timeout=_EXTRACT_TIMEOUT) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        logger.exception("vLLM への抽出リクエストに失敗しました")
        raise LlmRequestError(f"vLLM に接続できません: {exc}") from exc

    try:
        message = body["choices"][0]["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError):
        logger.error("vLLM の応答形式が想定外です: %r", body)
        return []

    if not content or not str(content).strip():
        logger.error("vLLM が空の content を返しました")
        return []

    return _parse_extraction_json(str(content)).items


def _parse_extraction_json(raw: str) -> ExtractionResult:
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"(\{.*\}|\[.*\])", stripped, flags=re.DOTALL)
    if match:
        stripped = match.group(1)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        logger.exception("抽出結果が JSON ではありません: %s", raw[:300])
        return ExtractionResult(items=[])

    if isinstance(data, list):
        data = {"items": data}
    try:
        return ExtractionResult.model_validate(data)
    except Exception:
        logger.exception("抽出結果のスキーマが合いません")
        return ExtractionResult(items=[])


def knowledge_row_from_extracted(
    item: ExtractedKnowledge,
    *,
    data_source_id: UUID | None,
    status: str = KnowledgeStatus.DRAFT.value,
) -> KnowledgeUnitTable:
    dump = item.model_dump()
    search_text = generate_search_text_from_mapping(dump)
    settings = get_settings()
    return KnowledgeUnitTable(
        data_source_id=data_source_id,
        knowledge_type=dump["knowledge_type"],
        title=dump["title"],
        situation=dump["situation"],
        problem=dump["problem"],
        judgment=dump["judgment"],
        action=dump["action"],
        reasoning=dump["reasoning"],
        outcome=dump["outcome"],
        lesson=dump["lesson"],
        applicable_situations=dump["applicable_situations"],
        limitations=dump["limitations"],
        industry=dump["industry"],
        product=dump["product"],
        sales_stage=dump["sales_stage"],
        search_text=search_text,
        embedding=generate_embedding(search_text),
        embedding_model=settings.embedding_model,
        status=status,
    )


class _SegmentLocator:
    """抽出に使った原文が、既存のどの発話にあたるかを引く。

    音声から取り込んだデータソースには、時刻つきの本物の発話が既に入っている。
    そこへ抽出のたびに合成セグメントを足すと、同じ内容が二重に並び、
    根拠を「何分何秒の発話か」まで辿れなくなる（CLAUDE.md 6章の出典要件）。

    照合は文字オフセットで行う。文字起こし本文は発話を順に連結したものなので、
    抽出に渡したチャンクは連結文字列の中にそのまま現れる。
    **人が本文を修正した場合は見つからない。** その場合は呼び出し側が
    従来どおり合成セグメントを作る（誤った発話に紐づけるよりは良い）。
    """

    def __init__(self, segments: list[UtteranceSegmentTable]) -> None:
        self._segments = segments
        self._starts: list[int] = []
        position = 0
        for segment in segments:
            self._starts.append(position)
            position += len(segment.content)
        self._joined = "".join(segment.content for segment in segments)

    def locate(self, excerpt: str) -> tuple[UtteranceSegmentTable, UtteranceSegmentTable] | None:
        if not self._segments or not excerpt:
            return None
        begin = self._joined.find(excerpt)
        if begin < 0:
            return None
        last = begin + len(excerpt) - 1
        return self._segments[self._index_of(begin)], self._segments[self._index_of(last)]

    def _index_of(self, offset: int) -> int:
        index = bisect_right(self._starts, offset) - 1
        return max(0, min(index, len(self._segments) - 1))


def process_text_to_knowledge(
    text: str,
    db: Session,
    data_source_id: UUID | None = None,
    *,
    status: str = KnowledgeStatus.DRAFT.value,
) -> tuple[list[KnowledgeUnitTable], list[str]]:
    """テキスト → 構造化 → search_text → embedding → DB。"""
    notes: list[str] = []
    if source_was_truncated(text):
        notes.append(
            "入力が長いため先頭から分割して抽出しました。後半が落ちている可能性があります。"
        )

    if data_source_id is None:
        source = DataSourceTable(source_type="manual")
        db.add(source)
        db.flush()
        data_source_id = source.id

    pairs = extract_knowledge_with_sources(text)
    saved: list[KnowledgeUnitTable] = []
    excerpt_to_segment: dict[str, UtteranceSegmentTable] = {}
    existing_segments = list(
        db.execute(
            select(UtteranceSegmentTable)
            .where(UtteranceSegmentTable.data_source_id == data_source_id)
            .order_by(UtteranceSegmentTable.sequence_no.asc())
        )
        .scalars()
        .all()
    )
    locator = _SegmentLocator(existing_segments)
    next_seq = (existing_segments[-1].sequence_no + 1) if existing_segments else 1

    for item, excerpt in pairs:
        if not item.title.strip():
            continue
        row = knowledge_row_from_extracted(
            item,
            data_source_id=data_source_id,
            status=status,
        )
        db.add(row)
        db.flush()

        # 既存の発話に対応づけられるならそちらを使う（音声から取り込んだ場合）。
        # 対応づかない場合だけ、原文を保持するための合成セグメントを作る
        span = locator.locate(excerpt)
        if span is None:
            segment = excerpt_to_segment.get(excerpt)
            if segment is None:
                segment = UtteranceSegmentTable(
                    data_source_id=data_source_id,
                    sequence_no=next_seq,
                    speaker="source",
                    start_sec=0.0,
                    end_sec=0.01,
                    content=excerpt,
                )
                next_seq += 1
                db.add(segment)
                db.flush()
                excerpt_to_segment[excerpt] = segment
            span = (segment, segment)

        db.add(
            KnowledgeEvidenceTable(
                knowledge_id=row.id,
                start_utterance_id=span[0].id,
                end_utterance_id=span[1].id,
            )
        )
        saved.append(row)
    db.commit()
    for row in saved:
        db.refresh(row)
    return saved, notes
