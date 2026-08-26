/**
 * 会話の保存。
 *
 * **なぜ保存するか。** 1回の回答に数十秒かかる。リロードやタブの閉じ忘れで
 * 消えると、同じ質問をもう一度待つことになる。
 *
 * **localStorage で足りる。** 認証を作らない方針（CLAUDE.md 3.1）のため
 * サーバ側に会話の持ち主を定義できず、DBに置き場所がない。
 * 端末内に閉じるぶんには持ち主の問題が起きない。
 */

import type { Turn } from "./useChat";

const KEY = "torano-maki:chat:v1";

/** 保存するターン数の上限。古い会話のために容量を使い切らないため */
const MAX_STORED_TURNS = 20;

export function loadTurns(): Turn[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // 保存後にTurnの形を変えた場合に備え、最低限の形だけ確認する。
    // 壊れた1件で画面全体が落ちる方が困る
    return parsed.filter(
      (t): t is Turn =>
        typeof t === "object" && t !== null && typeof (t as Turn).question === "string",
    );
  } catch {
    // プライベートモードや容量超過で読めないことがある。空で始めればよい
    return [];
  }
}

export function saveTurns(turns: Turn[]): void {
  try {
    // 進行中のターンは保存しない。復元しても続きは受け取れず、
    // 「回答中」のまま止まったものが残るだけになる
    const finished = turns
      .filter((t) => t.status === "done" || t.status === "error" || t.status === "aborted")
      .slice(-MAX_STORED_TURNS);
    localStorage.setItem(KEY, JSON.stringify(finished));
  } catch {
    // 保存できなくても会話そのものは続けられる
  }
}
