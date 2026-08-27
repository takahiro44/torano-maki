"""抽出済みナレッジを、利用者と対話しながら直す。

**なぜ抽出をやり直さないか。** 原文から取り直すと、人が手で直した箇所まで
巻き戻る。直したいのは1項目であることがほとんどで、全部を作り直すのは
「もう一度抽出する」であって「一緒に直す」ではない。

**DBに書かない。** 提案を返すだけにして、保存するかどうかは画面の人が決める。
AIが勝手に上書きすると、直前の値が どこにも残らない。

**変わった項目はサーバ側で比較して決める。** LLMに「どこを直したか」を
申告させると、直していない項目を直したと言ったり、その逆が起きる。
値そのものを比べれば嘘が入らない。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models.knowledge import (
    CBR_FIELD_LABELS,
    KnowledgeDraft,
    KnowledgeRefineProposal,
    KnowledgeRefineRequest,
    KnowledgeRefineResponse,
)
from app.services.llm_client import LlmRequestError, chat_completion

logger = logging.getLogger(__name__)

_SCHEMA_NAME = "knowledge_refine"

# 27Bモデルで13項目を書き直させると1往復で1分近くかかることがある。
# extraction.py が180秒を採っているのと同じ理由（DGXは4人で共有している）。
_REFINE_TIMEOUT = 180.0

# 原文をそのまま全部渡すと、直したい1件と関係のない話まで文脈に入り、
# 「原文に書いてあるから」と別の話を混ぜてくる。根拠として足りる長さに切る
_MAX_SOURCE_CHARS = 6000

# 相談が長引いても、古いやりとりまで毎回送るとプロンプトが膨らむ。
# 現在の値は毎回そのまま渡しているので、直近の流れが分かれば足りる
_MAX_HISTORY_MESSAGES = 12

_FIELD_LABELS: dict[str, str] = dict(CBR_FIELD_LABELS)

_SYSTEM_PROMPT = """あなたは営業ナレッジの編集者です。
利用者と一緒に、抽出済みのナレッジを直します。

## 守ること

- **事実を足さない。** 原文にも利用者の指示にも無いことは書かない。
  情報が足りなくて直せないなら、埋めずに comment でそう伝える
- **固有名詞（会社名・人名・地名・製品名）と数値（金額・人数・期間・時刻）を消さない**
- **指示された箇所だけを直す。** 指示されていない項目は、渡された現在の値を
  一字も変えずにそのまま返す（言い換え・整形もしない）
- 情報が無い項目は null にする。一般論やきれいな言い換えで埋めない
- 各項目は1〜3文で簡潔に。「〜が望ましい」ではなく、何をしたか・何が起きたかを書く
- title は30文字程度の、検索しやすい見出し
- comment は利用者への返事を1〜2文。何をどう直したか、
  直せなかったならその理由を書く。JSONやコード片を comment に入れない
- 出力は指定の JSON Schema に厳密に従う。説明文やコードフェンスは付けない

## 項目の意味

- situation: 状況（何が起きたか）
- problem: 顧客課題（顧客側の障壁・懸念）
- judgment: 判断（何を考え、どう判断したか）
- action: 行動（具体的に何をしたか）
- reasoning: 理由（なぜその判断・行動を選んだか）
- outcome: 結果（どうなったか）
- lesson: 学び（次に同じ場面が来たら何をするか）
- applicable_situations: 適用場面（どんな場面で使えるか）
- limitations: 制約・非適用場面（使えない場面や注意点）
- industry / product / sales_stage: 業界・商材・商談フェーズ（該当する場合のみ）
"""


def refine_json_schema() -> dict[str, Any]:
    return KnowledgeRefineProposal.model_json_schema()


def build_refine_messages(request: KnowledgeRefineRequest) -> list[dict[str, Any]]:
    """相談1往復ぶんのメッセージを組み立てる。

    **現在の値は毎回いちばん新しいものを渡す。** 履歴の中の提案ではなく、
    人が反映した結果が正になる（提案を見送った場合、履歴だけを見ると
    採用されたように読めてしまう）。
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for turn in request.history[-_MAX_HISTORY_MESSAGES:]:
        messages.append({"role": turn.role, "content": turn.content})

    source = (request.source_text or "").strip()
    if len(source) > _MAX_SOURCE_CHARS:
        source = source[:_MAX_SOURCE_CHARS] + "…（以下省略）"

    # 原文が無いのは音声由来でない登録や、原文を消したあとの再相談。
    # 「原文欄が空」ではなく「原文が無い」と伝えないと、モデルが探しに行く
    source_block = source or "（原文は残っていません。いまのナレッジと指示だけで判断してください）"

    messages.append(
        {
            "role": "user",
            "content": (
                "## いまのナレッジ（この値を出発点にしてください）\n"
                f"{_draft_as_text(request.draft)}\n\n"
                "## もとの原文\n"
                f"{source_block}\n\n"
                "## 直してほしいこと\n"
                f"{request.instruction}\n\n"
                "直した結果を全項目そろえて返してください。"
                "指示に関係しない項目は、いまの値をそのまま入れてください。"
            ),
        }
    )
    return messages


def _draft_as_text(draft: KnowledgeDraft) -> str:
    """日本語のラベル付きで渡す。

    JSONのまま渡すと、モデルが応答までJSONの調子に引きずられて
    comment にキー名が混ざる。読ませる側は人の文章の形にしておく。
    """
    lines: list[str] = []
    for name, value in draft.model_dump().items():
        label = _FIELD_LABELS.get(name, name)
        text = (value or "").strip() if isinstance(value, str) else ""
        lines.append(f"- {label}（{name}）: {text or '（未記入）'}")
    return "\n".join(lines)


def _normalized(value: str | None) -> str:
    return (value or "").strip()


def changed_field_names(before: KnowledgeDraft, after: KnowledgeDraft) -> list[str]:
    b = before.model_dump()
    a = after.model_dump()
    return [name for name in a if _normalized(a[name]) != _normalized(b[name])]


def refine_knowledge(request: KnowledgeRefineRequest) -> KnowledgeRefineResponse:
    """利用者の指示でナレッジを書き直した案を返す。保存はしない。"""
    body = chat_completion(
        build_refine_messages(request),
        temperature=0.2,
        timeout=_REFINE_TIMEOUT,
        json_schema=refine_json_schema(),
        schema_name=_SCHEMA_NAME,
    )

    try:
        content = body["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError):
        logger.error("vLLM の応答形式が想定外です: %r", body)
        raise LlmRequestError("AIの応答の形式が想定外でした") from None

    parsed = _parse_proposal(str(content or ""))
    if parsed is None:
        # 握り潰して「変更なし」を返すと、失敗したことに気づけないまま
        # 人が保存してしまう。呼び出し側が502に振り分けられる形で送出する
        raise LlmRequestError("AIの応答を解釈できませんでした。もう一度試してください")

    return KnowledgeRefineResponse(
        comment=parsed.comment.strip(),
        proposal=parsed.proposal,
        changed_fields=changed_field_names(request.draft, parsed.proposal),
    )


def _parse_proposal(raw: str) -> KnowledgeRefineProposal | None:
    """コードフェンスや前置きが付いていても拾う。

    guided decoding を要求していても、モデルによっては前置きが混ざる。
    ここで諦めると相談が1往復まるごと無駄になる。
    """
    stripped = raw.strip()
    if not stripped:
        return None
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match:
        stripped = match.group(0)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        logger.exception("相談の応答が JSON ではありません: %s", raw[:300])
        return None
    try:
        return KnowledgeRefineProposal.model_validate(data)
    except Exception:
        logger.exception("相談の応答のスキーマが合いません: %s", raw[:300])
        return None
