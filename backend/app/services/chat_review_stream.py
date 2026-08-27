"""「まとめる」の実況版。SSE で流すイベント列を作る。

**なぜ別ファイルか。** `chat_review.py` は `POST /chat-reviews/summarize` と
`POST /chat-reviews` が使っており、壊すと保存側ごと落ちる。
制御の流れが「結果を返す」から「イベントを産む」に変わるため、
`agent_stream.py` が `agent_loop.py` と分かれているのと同じ形にする。
要約の生成そのものは `chat_review.py` から**インポートして共有**する。

**なぜ実況が要るか。** 要約は vLLM への1往復で数十秒かかり、その間
画面は「まとめています…」の一行しか出せていなかった。加えてこの実装では
疑問点ごとにナレッジDBを引く（計画 3.3）ため、待ち時間はさらに伸びる。

**演じない。** 出しているのは実際に踏んだ工程だけで、割合や残り時間は
出さない（ExtractionProgress.tsx と同じ理由）。検索の件数と類似度は
実測値であり、上司が判定を検証できる材料になる。

**照合そのものは `chat_review.match_gap` に置いている。** 送信時にも同じ判定が
要るため（ヒアリングで本人が足した問いを当てる）、ここに持つと2箇所に
同じ閾値が並び、画面と保存結果が食い違う。
"""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy.orm import Session

from app.models.chat import ChatMessage
from app.models.chat_review import (
    ChatReviewDiagnosis,
    GapDbState,
    GapDiagnosis,
    ReviewStreamDoneEvent,
    ReviewStreamEvent,
    ReviewStreamStepEvent,
    ReviewStreamStepResultEvent,
)
from app.services.chat_review import generate_chat_review_summary, match_gap

logger = logging.getLogger(__name__)

# 照合する疑問点の上限。1件ごとに埋め込み生成＋検索が走るため、
# 増やすと「まとめる」の待ち時間がそのまま伸びる（計画 3.7）
_MAX_GAPS_TO_MATCH = 4

# 画面に出す疑問点の文字数。長い1文がそのまま label になると行が折り返して読めない
_GAP_LABEL_LIMIT = 28


def _shorten(text: str, limit: int = _GAP_LABEL_LIMIT) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def stream_chat_review_diagnosis(
    db: Session, messages: list[ChatMessage]
) -> Generator[ReviewStreamEvent]:
    """会話ログ → 要約 → 疑問点ごとのナレッジDB照合 を、工程ごとに流す。

    例外は投げたまま抜ける。`LlmNotConfiguredError` / `LlmRequestError` を
    `error` イベントへ振り分けるのは API 層の仕事（chat.py と同じ）。
    """
    step = 1
    yield ReviewStreamStepEvent(
        step=step, label=f"会話ログを読んでいます（{len(messages)}件のやりとり）"
    )
    summary = generate_chat_review_summary(messages)
    yield ReviewStreamStepResultEvent(
        step=step,
        ok=True,
        summary=(
            f"理解できていた事項を{len(summary.understood_points)}件、"
            f"埋まらなかった疑問を{len(summary.knowledge_gaps)}件 読み取りました"
        ),
    )

    gaps: list[GapDiagnosis] = []
    for gap in summary.knowledge_gaps[:_MAX_GAPS_TO_MATCH]:
        step += 1
        yield ReviewStreamStepEvent(
            step=step, label=f"「{_shorten(gap)}」をナレッジDBで探しています"
        )
        diagnosis = match_gap(db, gap)
        gaps.append(diagnosis)
        yield ReviewStreamStepResultEvent(step=step, ok=True, summary=_describe(diagnosis))

    # 上司の時間を使う価値があるのは missing の方。並び順はサーバが決める（計画 3.4）
    gaps.sort(key=lambda g: 0 if g.db_state is GapDbState.MISSING else 1)

    yield ReviewStreamDoneEvent(
        diagnosis=ChatReviewDiagnosis(
            summary=summary.summary,
            understood_points=summary.understood_points,
            gaps=gaps,
        )
    )


def _describe(diagnosis: GapDiagnosis) -> str:
    """工程1行。**件数と類似度は実測値をそのまま出す。**"""
    if diagnosis.db_state is GapDbState.MISSING:
        return "近いナレッジは見つかりませんでした（上司にしか無い知見）"
    top = diagnosis.existing_knowledge[0]
    score = f"{top.semantic_score:.2f}" if top.semantic_score is not None else "―"
    return f"既存ナレッジが{len(diagnosis.existing_knowledge)}件（最も近いもの 類似度{score}）"
