/**
 * 回答のあとに「次にできること」を決める。
 *
 * **LLMに決めさせない。** 何ができるかは、そのターンで実際に起きたこと
 * （出典が付いたか、Toolが何を返したか）から機械的に決まる。モデルに
 * 提案させると、プロンプトが伸びてTTFTが悪化する（useChat.ts の
 * SEARCH_TOP_K の実測）うえ、存在しない操作を提案しても検証できない。
 * 出典を本文から拾わない判断（agent_loop.py）と同じ理由である。
 *
 * **判定はここだけに置く。** phase.ts と同じで、画面の各所が独自に
 * 「今なにができるか」を判定し始めると、同じ状態で別のボタンが出る。
 */

import { openWith } from "../../lib/handoff";
import { navigate, roleplayStartPath } from "../../lib/router";
import type { Turn } from "./useChat";

export type NextAction = {
  key: string;
  label: string;
  /** ボタンの下に出す一行。何が起きるか分からないまま押させない */
  hint: string;
  /** primary は1ターンに1つだけ。押してほしいものが2つあると選べなくなる */
  tone: "primary" | "quiet";
  run: () => void;
};

export function nextActions(turn: Turn, onReview: () => void): NextAction[] {
  // 進行中・中止・失敗のターンに次の一手は無い。失敗には「もう一度試す」がある
  if (turn.status !== "done") return [];

  const actions: NextAction[] = [];
  const found = turn.citations.length > 0;

  if (found) {
    // 先頭の出典を使う。検索結果はスコア順なので、最も近い場面が主役になる
    const first = turn.citations[0];
    actions.push({
      key: "roleplay",
      label: "この場面を練習する",
      hint: first.title,
      tone: "primary",
      // 質問文も連れて行く。ナレッジIDだけだと、本人が引っかかった一点が
      // 消えて事例のタイトルだけから場面が作られる（router.ts の理由）
      run: () =>
        navigate(roleplayStartPath({ knowledgeId: first.knowledge_id, query: turn.question })),
    });
  }

  actions.push({
    key: "input",
    // 出典が無かったということは、ナレッジDBにこの話題が無いということ。
    // 「答えられませんでした」で終わらせず、埋める側へ回ってもらう
    label: found ? "自分の経験も登録する" : "この話題を登録する",
    hint: found ? "同じ場面での自分の判断を残す" : "蓄積に無い話題でした",
    tone: found ? "quiet" : "primary",
    run: () => openWith("/input", { note: turn.question }),
  });

  actions.push({
    key: "review",
    label: "上司に確認する",
    hint: "この会話をまとめて送る",
    tone: "quiet",
    run: onReview,
  });

  return actions;
}
