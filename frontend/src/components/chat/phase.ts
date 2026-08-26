/**
 * AIが今どの段階にいるか。
 *
 * **1箇所で決める。** 調査ビューの見出し、ログ、アシスタントの機嫌が
 * それぞれ独自に「忙しいかどうか」を判定すると、同じ瞬間に別のことを
 * 言い出す。判定はここだけに置き、画面はその結果を読むだけにする。
 */

import type { Turn } from "./useChat";

export type Phase = "idle" | "planning" | "searching" | "answering" | "done" | "error";

/**
 * 「回答中」だけで済ませない。
 *
 * 質問を送ってから最初の検索が始まるまでに数秒ある。そこを「回答中」と
 * 書くと、実際にはまだ何も調べていないのに調べ終わったように見える。
 */
export function currentPhase(turn: Turn | null): Phase {
  if (!turn) return "idle";
  if (turn.status === "error") return "error";
  if (turn.status !== "streaming") return "done";
  if (turn.steps.some((s) => s.ok === null)) return "searching";
  return turn.answer.length > 0 ? "answering" : "planning";
}
