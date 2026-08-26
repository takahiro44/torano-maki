"""根拠付きマイクロロープレ。

**商談全体を再現しない。** 値引き要求や反対意見のように、
判断が必要な一場面だけを1〜3分で反復するのがこの機能の単位である
（計画書1章）。長時間の再現は待ち時間ばかり増え、どこを直せばよいかも
分からなくなるため、意図的に切り捨てている。

Qwen の役割を3つに分け、1つの巨大なプロンプトにまとめない（計画書7章）。

1. シナリオ生成 … 何を練習するかを決める。1回だけ
2. 顧客役      … それまでの会話だけを見て短く返す。Tool は使わない
3. フィードバック … rubric と実際の発話を突き合わせて講評する

**出典 ID は一度も LLM に出させない。** シナリオにも顧客役の返答にも
Knowledge ID を含めず、画面に出す出典は `roleplay_session_knowledge` に
保存した行だけから組み立てる。LLM に書かせると、それらしい ID を捏造し、
利用者が検証できなくなる（CLAUDE.md 6章）。

検索はセッション作成時にサーバ側で1回だけ行う。顧客役に Tool Calling を
持たせないのは、1往復あたりの待ち時間が体感を壊すためと、
会話の途中で別のナレッジを拾ってきて人格が変わるのを防ぐため。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.roleplay import (
    CATEGORY_QUERIES,
    CustomerReply,
    GeneratedFeedback,
    InputMode,
    LearnerTurnRequest,
    ReferencedKnowledge,
    ReferencedUtterance,
    RoleplayFeedback,
    RoleplayScenario,
    RoleplaySession,
    RoleplaySessionCreate,
    RoleplayTurn,
    RubricResult,
    SessionStatus,
    TurnRole,
    UsageType,
)
from app.models.tables import (
    DataSourceTable,
    RoleplayFeedbackTable,
    RoleplaySessionKnowledgeTable,
    RoleplaySessionTable,
    RoleplayTurnTable,
)
from app.services.knowledge_context import (
    KnowledgeContext,
    KnowledgeContextError,
    build_knowledge_context,
)
from app.services.llm_client import chat_completion
from app.services.search import search_knowledge

logger = logging.getLogger(__name__)

# 1セッションへ渡す Knowledge の上限。
#
# 増やすほど Qwen が「根拠にない設定」を作る余地が広がり、
# どの事例を練習しているのかも曖昧になる。計画書16章のリスク対策。
MAX_KNOWLEDGE_PER_SESSION = 3

# 検索で見る候補数。Evidence を持たないナレッジを飛ばすため、
# 採用したい件数より多めに引く。
_SEARCH_CANDIDATES = 8

# プロンプトへ載せる1ナレッジあたりの発話数。
# Evidence が商談のほぼ全体を指す場合があり、全部載せると場面が埋もれる。
_MAX_PROMPT_UTTERANCES = 12

# 画面の出典に出す1ナレッジあたりの発話数。
_MAX_REFERENCE_UTTERANCES = 8

# 生成ごとのタイムアウト。用途で待てる長さが違うため個別に持つ（計画書15章）。
_SCENARIO_TIMEOUT = 120.0
_CUSTOMER_TIMEOUT = 60.0
_FEEDBACK_TIMEOUT = 120.0


class RoleplayError(RuntimeError):
    """想定内の業務エラー。

    `code` で HTTP ステータスへ振り分ける。文言で分岐すると、
    メッセージを直した瞬間にステータスが変わる。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RoleplayGenerationError(RuntimeError):
    """Qwen の出力が契約に合わなかったときに上げる。

    接続失敗（LlmRequestError）と分けているのは、対処が違うため。
    こちらはサーバは生きていて、出力だけが想定外という状態を指す。
    """


_SCENARIO_SYSTEM_PROMPT = """あなたは営業研修の設計者です。
社内の実際の商談記録をもとに、新人が1〜3分で練習できる「一場面」を作ります。

## 守ること

- 与えられた事例に書かれていない事実、顧客名、金額、成果を作らない
- 商談全体ではなく、判断が必要な一場面だけを切り出す
- opening_line は顧客の発言。この一言から練習が始まる
- opening_line に模範解答のヒントを混ぜない（顧客は答えを知らない）
- learner_goal は「何をすれば成功か」を1文で書く
- rubric は2〜3個。この場面で見るべき行動だけに絞る
- rubric の key は英小文字とアンダースコアのみ
- 固有名詞や機密情報はそのまま使わず、業種・役職の表現に置き換える
- 出力は指定の JSON Schema に厳密に従う。説明文やコードフェンスは付けない
"""

_CUSTOMER_SYSTEM_PROMPT = """あなたはロープレの顧客役です。営業ではありません。

## 守ること

- 返答は1〜3文、150文字以内
- 設定された顧客像を最後まで変えない
- 模範解答や「こう言ってほしい」というヒントを出さない
- 営業が良い質問をしたら少しだけ情報を出す。まだ全ては明かさない
- 社内事例の固有名詞や機密情報を口に出さない
- 説明やト書きを書かない。顧客のセリフだけを返す
- 出力は指定の JSON Schema に厳密に従う
"""

_FEEDBACK_SYSTEM_PROMPT = """あなたは営業の指導役です。
新人の練習を、社内の実際の事例と比べて講評します。

## 守ること

- 新人が実際に言ったことだけを評価する。言っていないことを補わない
- 総合点や「共感力82点」のような数値評価を作らない
- rubric の key ごとに met / partial / not_met を付ける
- rubric に無い観点を増やさない
- 事例に書かれていない事実、成果、顧客属性を作らない
- next_phrase は次の商談でそのまま口に出せる一言にする
- improvements は「何をすればよかったか」を具体的な行動で書く
- 出力は指定の JSON Schema に厳密に従う。説明文やコードフェンスは付けない
"""


# ---------------------------------------------------------------------------
# LLM 呼び出しの共通処理
# ---------------------------------------------------------------------------


def _parse_json_object(raw: str) -> dict[str, Any]:
    """モデルの応答から JSON を取り出す。

    guided decoding を使っていても、コードフェンスや前置きが混ざることがある。
    `extraction.py` にも同じ後始末があるが、あちらは抽出専用の
    `ExtractionResult` を返す形に閉じており再利用できない。
    担当領域が違うため統合はせず（CLAUDE.md 4.5）、ここでは辞書だけを返す。
    """
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match:
        stripped = match.group(0)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise RoleplayGenerationError(f"応答がJSONではありません: {raw[:200]}") from exc
    if not isinstance(data, dict):
        raise RoleplayGenerationError("応答がJSONオブジェクトではありません")
    return data


def _complete_structured[TModel: BaseModel](
    messages: list[dict[str, Any]],
    model_cls: type[TModel],
    *,
    schema_name: str,
    temperature: float,
    timeout: float,
) -> TModel:
    """Qwen に構造化出力を1回要求し、Pydantic で検証して返す。

    **検証に失敗したら握り潰さない。** 余分なキーや欠けた項目を
    黙って通すと、シナリオに無い設定や評価対象のすり替えが
    画面まで届いてしまう。
    """
    body = chat_completion(
        messages,
        temperature=temperature,
        timeout=timeout,
        json_schema=model_cls.model_json_schema(),
        schema_name=schema_name,
    )
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RoleplayGenerationError(f"応答形式が想定外です: {body}") from exc
    if not content or not str(content).strip():
        raise RoleplayGenerationError("応答が空でした")

    try:
        return model_cls.model_validate(_parse_json_object(str(content)))
    except ValidationError as exc:
        raise RoleplayGenerationError(f"応答が契約に合いません: {exc}") from exc


# ---------------------------------------------------------------------------
# ナレッジの選定
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectedKnowledge:
    """セッションで使うと決めたナレッジ1件。"""

    context: KnowledgeContext
    rank: int
    usage_type: UsageType


def _effective_query(payload: RoleplaySessionCreate) -> str:
    """検索に使う文を決める。

    カテゴリは短すぎて検索が効かないため、`CATEGORY_QUERIES` の
    言い回しへ展開する（理由は models/roleplay.py）。
    """
    if payload.query:
        return payload.query
    if payload.category is not None:
        return CATEGORY_QUERIES[payload.category]
    return "商談で判断が必要になった場面"


def _load_usable_context(db: Session, knowledge_id: UUID) -> KnowledgeContext | None:
    """根拠まで揃っているナレッジだけを通す。

    Evidence が無い、または壊れているナレッジで練習させると、
    フィードバックの「元の発話」を示せない。それでは根拠付きロープレに
    ならないので、候補から外して次を探す。
    """
    try:
        context = build_knowledge_context(db, knowledge_id, include_summary=True)
    except KnowledgeContextError as exc:
        logger.info("ロープレ候補から除外: knowledge_id=%s code=%s", knowledge_id, exc.code)
        return None
    return context if context.has_evidence else None


def select_knowledge(db: Session, payload: RoleplaySessionCreate) -> list[SelectedKnowledge]:
    """練習に使うナレッジを最大3件選ぶ。

    `knowledge_id` の指定があればそれを primary に固定する。
    AIチャットの回答から「この場面を練習する」で入ってきた場合、
    利用者が見ていたナレッジと別のもので練習が始まると話が繋がらないため。
    """
    selected: list[SelectedKnowledge] = []
    used: set[UUID] = set()

    if payload.knowledge_id is not None:
        primary = _load_usable_context(db, payload.knowledge_id)
        if primary is None:
            raise RoleplayError(
                "no_evidence",
                "このナレッジには根拠となる発話が紐づいていないため、練習を開始できません",
            )
        selected.append(SelectedKnowledge(context=primary, rank=1, usage_type=UsageType.PRIMARY))
        used.add(payload.knowledge_id)

    query = _effective_query(payload)
    for hit in search_knowledge(db, query, _SEARCH_CANDIDATES):
        if len(selected) >= MAX_KNOWLEDGE_PER_SESSION:
            break
        if hit.id in used:
            continue
        context = _load_usable_context(db, hit.id)
        if context is None:
            continue
        used.add(hit.id)
        selected.append(
            SelectedKnowledge(
                context=context,
                rank=len(selected) + 1,
                usage_type=UsageType.PRIMARY if not selected else UsageType.SUPPORTING,
            )
        )

    if not selected:
        raise RoleplayError(
            "no_evidence",
            "根拠となる発話を持つ確認済みナレッジが見つかりませんでした。"
            "商談を取り込んでナレッジを confirmed にしてから練習してください",
        )
    return selected


# ---------------------------------------------------------------------------
# プロンプトの組み立て
# ---------------------------------------------------------------------------


def _format_seconds(value: float) -> str:
    return f"{int(value // 60)}:{int(value % 60):02d}"


# 話者ラベルの日本語表記。
#
# **DBの生の値をそのまま渡さない。** `salesperson` / `customer` は
# Qwenでも読めるが、日本語のプロンプトの中で英語の識別子が混ざると
# 「顧客役を演じる」という指示との対応が弱くなる。
# `source` は抽出時に作られた合成セグメントで、話者が特定できていない。
_SPEAKER_LABELS: dict[str, str] = {
    "salesperson": "営業",
    "customer": "顧客",
    "source": "不明",
    "unknown": "不明",
}


def _speaker_label(speaker: str) -> str:
    return _SPEAKER_LABELS.get(speaker, "不明")


def _format_utterances(context: KnowledgeContext, limit: int) -> list[str]:
    """発話を番号・話者・時刻つきで並べる。

    **話者ラベルは信用してよい。** 商談データの投入経路が話者を保持する
    ようになり、実測で営業593 / 顧客485 / 不明155（12%）まで判明している。
    以前は全件 unknown だったため「話者は不確かである」と伝えていたが、
    それを続けると使える情報をQwenに捨てさせることになる。

    不明が残る分は「不明」と表示し、Qwenが役割を推測で埋めないようにする。
    """
    lines: list[str] = []
    for span in context.spans:
        for utterance in span.utterances:
            if len(lines) >= limit:
                return lines
            mark = "★" if utterance.is_evidence else "　"
            lines.append(
                f"{mark}#{utterance.sequence_no} [{_speaker_label(utterance.speaker)}] "
                f"{_format_seconds(utterance.start_sec)} {utterance.content}"
            )
    return lines


def _format_knowledge_block(item: SelectedKnowledge) -> str:
    """1ナレッジぶんの材料。CBR項目と実際の発話を並べる。"""
    row = item.context.knowledge
    fields = [
        ("状況", row.situation),
        ("顧客課題", row.problem),
        ("判断", row.judgment),
        ("行動", row.action),
        ("理由", row.reasoning),
        ("結果", row.outcome),
        ("学び", row.lesson),
        ("適用場面", row.applicable_situations),
        ("使えない場面", row.limitations),
        ("業界", row.industry),
        ("商材", row.product),
        ("商談フェーズ", row.sales_stage),
    ]
    parts = [f"### 事例{item.rank}: {row.title}"]
    parts.extend(f"- {label}: {value}" for label, value in fields if value)

    utterances = _format_utterances(item.context, _MAX_PROMPT_UTTERANCES)
    if utterances:
        parts.append(
            "- 実際の発話（★が根拠範囲。[営業]/[顧客]は確認済み。[不明]は推測で埋めないこと）:"
        )
        parts.extend(f"  {line}" for line in utterances)

    summary = item.context.summary
    if summary is not None:
        parts.append(f"- 商談全体の要約: {summary.summary}")
    return "\n".join(parts)


def _format_knowledge_materials(items: list[SelectedKnowledge]) -> str:
    return "\n\n".join(_format_knowledge_block(item) for item in items)


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def generate_scenario(
    query: str, items: list[SelectedKnowledge], *, max_turns: int
) -> RoleplayScenario:
    """練習シナリオを1回で作る。

    `max_turns` は生成後にサーバ側の値で上書きする。モデルに決めさせると、
    ラウンドロビン用の1往復モードを指定しても2往復のシナリオが返り、
    デモの時間管理ができなくなる。
    """
    user_content = (
        f"新人からの相談: {query}\n\n"
        f"以下の社内事例をもとに、練習する一場面を1つ作ってください。\n"
        f"新人が発言できる回数は{max_turns}回です。\n\n"
        f"{_format_knowledge_materials(items)}"
    )
    scenario = _complete_structured(
        [
            {"role": "system", "content": _SCENARIO_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        RoleplayScenario,
        schema_name="roleplay_scenario",
        temperature=0.4,
        timeout=_SCENARIO_TIMEOUT,
    )
    return scenario.model_copy(update={"max_turns": max_turns})


def generate_customer_reply(
    scenario: RoleplayScenario,
    turns: list[RoleplayTurnTable],
    learner_content: str,
    *,
    is_final: bool,
) -> str:
    """顧客役の返答を作る。

    渡すのはシナリオとそれまでの会話だけ。ナレッジ本体を渡さないのは、
    顧客が模範解答を知っている状態になり、練習にならないため（計画書7.2）。
    """
    history: list[dict[str, Any]] = [
        {"role": "system", "content": _CUSTOMER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"## 場面\n{scenario.situation}\n\n"
                f"## あなた（顧客）\n{scenario.customer_persona}\n\n"
                "この設定で顧客役を演じてください。"
            ),
        },
    ]
    # 顧客の発言を assistant、営業の発言を user に写す。
    # 顧客役から見た会話の向きに合わせないと、モデルが自分の発言を
    # 相手の発言と取り違えて人格が入れ替わる。
    for turn in turns:
        role = "assistant" if turn.role == TurnRole.CUSTOMER else "user"
        history.append({"role": role, "content": turn.content})
    history.append({"role": "user", "content": learner_content})

    if is_final:
        history.append(
            {
                "role": "user",
                "content": (
                    "これが最後のやりとりです。会話を引き延ばさず、"
                    "顧客として自然に区切りをつける返答を1〜2文でしてください。"
                ),
            }
        )

    reply = _complete_structured(
        history,
        CustomerReply,
        schema_name="roleplay_customer_reply",
        temperature=0.7,
        timeout=_CUSTOMER_TIMEOUT,
    )
    return reply.content.strip()


def _align_rubric_results(
    scenario: RoleplayScenario, results: list[RubricResult]
) -> list[RubricResult]:
    """rubric に無い観点を落とし、`label` をシナリオ側の値で埋める。

    観点名まで Qwen に書かせると、評価対象が静かにすり替わる。
    出典 ID を信用しないのと同じ理由で、キーの照合はサーバが行う。
    """
    labels = {item.key: item.label for item in scenario.rubric}
    aligned: list[RubricResult] = []
    seen: set[str] = set()
    for result in results:
        label = labels.get(result.key)
        if label is None or result.key in seen:
            continue
        seen.add(result.key)
        aligned.append(result.model_copy(update={"label": label}))
    if not aligned:
        raise RoleplayGenerationError("フィードバックがシナリオの評価観点と対応していません")
    return aligned


def generate_feedback(
    scenario: RoleplayScenario,
    turns: list[RoleplayTurnTable],
    items: list[SelectedKnowledge],
) -> GeneratedFeedback:
    """練習全体を社内事例と比べて講評する。"""
    transcript = "\n".join(
        f"{'新人' if turn.role == TurnRole.LEARNER else '顧客'}: {turn.content}" for turn in turns
    )
    rubric_lines = "\n".join(f"- {item.key}: {item.label}" for item in scenario.rubric)
    user_content = (
        f"## 練習した場面\n{scenario.title}\n{scenario.situation}\n\n"
        f"## 目標\n{scenario.learner_goal}\n\n"
        f"## 評価観点\n{rubric_lines}\n\n"
        f"## 実際のやりとり\n{transcript}\n\n"
        f"## 比較する社内事例\n{_format_knowledge_materials(items)}\n\n"
        "評価観点ごとに判定し、講評してください。"
    )
    feedback = _complete_structured(
        [
            {"role": "system", "content": _FEEDBACK_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        GeneratedFeedback,
        schema_name="roleplay_feedback",
        temperature=0.2,
        timeout=_FEEDBACK_TIMEOUT,
    )
    return feedback.model_copy(
        update={"rubric_results": _align_rubric_results(scenario, feedback.rubric_results)}
    )


# ---------------------------------------------------------------------------
# セッションの状態
# ---------------------------------------------------------------------------


def scenario_of(session: RoleplaySessionTable) -> RoleplayScenario:
    """保存済みスナップショットからシナリオを復元する。"""
    try:
        return RoleplayScenario.model_validate(session.scenario)
    except ValidationError as exc:  # 保存時に検証済みなので通常は起きない
        raise RoleplayError("invalid_scenario", "保存されたシナリオを読み取れません") from exc


def _turns_of(db: Session, session_id: UUID) -> list[RoleplayTurnTable]:
    return list(
        db.execute(
            select(RoleplayTurnTable)
            .where(RoleplayTurnTable.session_id == session_id)
            .order_by(RoleplayTurnTable.sequence_no)
        )
        .scalars()
        .all()
    )


def _learner_turn_count(turns: list[RoleplayTurnTable]) -> int:
    return sum(1 for turn in turns if turn.role == TurnRole.LEARNER)


def get_session(db: Session, session_id: UUID) -> RoleplaySessionTable:
    session = db.get(RoleplaySessionTable, session_id)
    if session is None:
        raise RoleplayError("session_not_found", "ロープレセッションが見つかりません")
    return session


def _link_knowledge(
    db: Session, session: RoleplaySessionTable, items: list[SelectedKnowledge]
) -> None:
    for item in items:
        db.add(
            RoleplaySessionKnowledgeTable(
                session_id=session.id,
                knowledge_id=item.context.knowledge.id,
                rank=item.rank,
                usage_type=item.usage_type.value,
            )
        )


def create_session(db: Session, payload: RoleplaySessionCreate) -> RoleplaySessionTable:
    """検索 → 根拠取得 → シナリオ生成 → 保存。

    **生成に失敗したらセッションを作らない。** 先に空のセッションを
    作ってから生成すると、シナリオの無い active なセッションが残り、
    画面がどう扱えばよいか分からない状態になる。
    """
    items = select_knowledge(db, payload)
    query = _effective_query(payload)
    scenario = generate_scenario(query, items, max_turns=payload.max_turns)

    session = RoleplaySessionTable(
        query=query,
        scenario=scenario.model_dump(mode="json"),
        status=SessionStatus.ACTIVE.value,
    )
    db.add(session)
    db.flush()

    _link_knowledge(db, session, items)
    # 顧客の最初の発言も1行として保存する。会話の並びを1箇所で作れるようにするため。
    db.add(
        RoleplayTurnTable(
            session_id=session.id,
            sequence_no=1,
            role=TurnRole.CUSTOMER.value,
            content=scenario.opening_line,
            input_mode=InputMode.GENERATED.value,
        )
    )
    db.commit()
    db.refresh(session)
    return session


def retry_session(db: Session, session: RoleplaySessionTable) -> RoleplaySessionTable:
    """同じ場面をもう一度。

    **シナリオを作り直さない。** 生成し直すと出題が変わり、
    「同じ場面で改善できたか」を比べられなくなる。生成待ちも消える。
    """
    scenario = scenario_of(session)
    links = list(
        db.execute(
            select(RoleplaySessionKnowledgeTable)
            .where(RoleplaySessionKnowledgeTable.session_id == session.id)
            .order_by(RoleplaySessionKnowledgeTable.rank)
        )
        .scalars()
        .all()
    )

    retry = RoleplaySessionTable(
        query=session.query,
        scenario=session.scenario,
        status=SessionStatus.ACTIVE.value,
    )
    db.add(retry)
    db.flush()

    for link in links:
        db.add(
            RoleplaySessionKnowledgeTable(
                session_id=retry.id,
                knowledge_id=link.knowledge_id,
                rank=link.rank,
                usage_type=link.usage_type,
            )
        )
    db.add(
        RoleplayTurnTable(
            session_id=retry.id,
            sequence_no=1,
            role=TurnRole.CUSTOMER.value,
            content=scenario.opening_line,
            input_mode=InputMode.GENERATED.value,
        )
    )
    db.commit()
    db.refresh(retry)
    return retry


def ensure_can_answer(db: Session, session: RoleplaySessionTable) -> RoleplayScenario:
    """まだ回答を受け付けられる状態かを確かめ、シナリオを返す。

    音声の受け口からも使う。**文字起こしを始める前に弾くため**に
    切り出している。数十秒かけてSTTを回した後で「もう発言できません」と
    返すのは、待たせた分だけ体験が悪い。
    """
    if session.status != SessionStatus.ACTIVE:
        raise RoleplayError("session_not_active", "このセッションは終了しています")

    scenario = scenario_of(session)
    used = _learner_turn_count(_turns_of(db, session.id))
    if used >= scenario.max_turns:
        raise RoleplayError(
            "max_turns_reached",
            f"発言できるのは{scenario.max_turns}回までです。振り返りへ進んでください",
        )
    return scenario


def add_learner_turn(
    db: Session, session: RoleplaySessionTable, request: LearnerTurnRequest
) -> RoleplaySessionTable:
    """後輩の回答を保存し、顧客役の返答まで進める。

    **顧客の返答を作ってから2件まとめて保存する。** 先に回答だけ保存すると、
    Qwen が落ちたときに「返事の来ない発言」がセッションに残り、
    利用者は同じ回答を送り直せなくなる。
    """
    scenario = ensure_can_answer(db, session)
    turns = _turns_of(db, session.id)
    used = _learner_turn_count(turns)

    is_final = used + 1 >= scenario.max_turns
    content = request.content.strip()
    reply = generate_customer_reply(scenario, turns, content, is_final=is_final)

    next_seq = len(turns) + 1
    db.add(
        RoleplayTurnTable(
            session_id=session.id,
            sequence_no=next_seq,
            role=TurnRole.LEARNER.value,
            content=content,
            input_mode=request.input_mode.value,
        )
    )
    db.add(
        RoleplayTurnTable(
            session_id=session.id,
            sequence_no=next_seq + 1,
            role=TurnRole.CUSTOMER.value,
            content=reply,
            input_mode=InputMode.GENERATED.value,
        )
    )
    db.commit()
    db.refresh(session)
    return session


def _selected_from_links(db: Session, session_id: UUID) -> list[SelectedKnowledge]:
    """保存済みの紐づけからナレッジ材料を組み直す。

    フィードバックも出典表示も、検索をやり直さずここを通す。
    作成時と別のナレッジで講評されると、利用者が見ている出典と食い違う。
    """
    links = list(
        db.execute(
            select(RoleplaySessionKnowledgeTable)
            .where(RoleplaySessionKnowledgeTable.session_id == session_id)
            .order_by(RoleplaySessionKnowledgeTable.rank)
        )
        .scalars()
        .all()
    )
    items: list[SelectedKnowledge] = []
    for link in links:
        try:
            context = build_knowledge_context(db, link.knowledge_id, include_summary=False)
        except KnowledgeContextError:
            # 練習の後にナレッジが削除・差し戻しされた場合。
            # 出典として示せないものは黙って落とす方が、壊れた出典を出すより良い。
            logger.info("出典から除外: knowledge_id=%s", link.knowledge_id)
            continue
        items.append(
            SelectedKnowledge(
                context=context, rank=link.rank, usage_type=UsageType(link.usage_type)
            )
        )
    return items


def create_feedback(db: Session, session: RoleplaySessionTable) -> RoleplayFeedbackTable:
    """フィードバックを生成して保存し、セッションを完了にする。

    既にある場合は作り直さずそれを返す。ダブルクリックや再送のたびに
    講評が変わると、利用者はどれが自分の結果なのか判断できない。
    """
    existing = db.get(RoleplayFeedbackTable, session.id)
    if existing is not None:
        return existing

    turns = _turns_of(db, session.id)
    if _learner_turn_count(turns) == 0:
        raise RoleplayError("no_learner_turn", "まだ回答がないため振り返りを作れません")

    scenario = scenario_of(session)
    generated = generate_feedback(scenario, turns, _selected_from_links(db, session.id))

    row = RoleplayFeedbackTable(
        session_id=session.id,
        rubric_result=[result.model_dump(mode="json") for result in generated.rubric_results],
        strengths=generated.strengths,
        improvements=generated.improvements,
        next_phrase=generated.next_phrase,
        focus_next_try=generated.focus_next_try,
    )
    db.add(row)
    session.status = SessionStatus.COMPLETED.value
    session.completed_at = func.now()
    db.commit()
    db.refresh(row)
    db.refresh(session)
    return row


# ---------------------------------------------------------------------------
# 画面へ返す形の組み立て
# ---------------------------------------------------------------------------


def _reference_of(db: Session, item: SelectedKnowledge) -> ReferencedKnowledge:
    row = item.context.knowledge
    file_name: str | None = None
    if row.data_source_id is not None:
        source = db.get(DataSourceTable, row.data_source_id)
        file_name = source.file_name if source is not None else None

    utterances: list[ReferencedUtterance] = []
    for span in item.context.spans:
        for utterance in span.utterances:
            if len(utterances) >= _MAX_REFERENCE_UTTERANCES:
                break
            if not utterance.is_evidence:
                continue
            utterances.append(
                ReferencedUtterance(
                    sequence_no=utterance.sequence_no,
                    speaker=utterance.speaker,
                    start_sec=utterance.start_sec,
                    end_sec=utterance.end_sec,
                    content=utterance.content,
                )
            )

    return ReferencedKnowledge(
        knowledge_id=row.id,
        title=row.title,
        usage_type=item.usage_type,
        rank=item.rank,
        data_source_id=row.data_source_id,
        file_name=file_name,
        applicable_situations=row.applicable_situations,
        limitations=row.limitations,
        utterances=utterances,
    )


def _feedback_of(row: RoleplayFeedbackTable | None) -> RoleplayFeedback | None:
    if row is None:
        return None
    return RoleplayFeedback(
        rubric_results=[RubricResult.model_validate(item) for item in row.rubric_result],
        strengths=list(row.strengths),
        improvements=list(row.improvements),
        next_phrase=row.next_phrase,
        focus_next_try=row.focus_next_try,
        created_at=row.created_at,
    )


def build_session_view(db: Session, session: RoleplaySessionTable) -> RoleplaySession:
    """画面が必要とするものを1つの型にまとめる。

    操作系のAPIも `GET` と同じ形を返す。画面が状態を継ぎ足しで持つと、
    再読込したときだけ表示が変わるような食い違いが起きる。
    """
    scenario = scenario_of(session)
    turns = _turns_of(db, session.id)
    used = _learner_turn_count(turns)

    return RoleplaySession(
        session_id=session.id,
        status=SessionStatus(session.status),
        query=session.query,
        scenario=scenario,
        turns=[RoleplayTurn.model_validate(turn) for turn in turns],
        references=[_reference_of(db, item) for item in _selected_from_links(db, session.id)],
        feedback=_feedback_of(db.get(RoleplayFeedbackTable, session.id)),
        learner_turns_used=used,
        remaining_learner_turns=max(0, scenario.max_turns - used),
        created_at=session.created_at,
        completed_at=session.completed_at,
    )
