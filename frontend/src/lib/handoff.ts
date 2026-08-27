/**
 * 会話から他の画面へ渡す持ち物。
 *
 * **URLに載せない。** 「この質問に答えるナレッジが無かったので登録画面を
 * 開く」ときに渡したいのは質問文そのもので、長さも中身もURLに向かない。
 * ナレッジIDのように短く、再読込で復元したいものはURLへ置く
 * （ロープレがそれ。router.ts の roleplayStartPath）。
 *
 * **状態はこのモジュールに1つだけ置く。** router.ts と同じ理由。
 * 渡す側（NextActions）と受け取る側（KnowledgeInput）が別々に覚えると、
 * 片方だけ古い質問文を持ち続ける。
 *
 * **別の画面が開かれた時点で消える。** 残しておくと、利用者が後から手で
 * 同じ画面を開き直したときに昔の質問文が蘇る。渡した瞬間の文脈にしか
 * 意味がない。
 */

import { useSyncExternalStore } from "react";
import { navigate } from "./router";

/** 受け取る側が解釈する。いまのところ「何がきっかけで開いたか」だけ */
export type Handoff = { note?: string };

type Pending = { path: string; handoff: Handoff };

let pending: Pending | null = null;

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  return () => {
    listeners.delete(onStoreChange);
  };
}

/** `pending` は差し替えでしか変わらない。同じ参照を返し続けるので再描画が暴れない */
function getPending(): Pending | null {
  return pending;
}

/** 持ち物を添えて画面を開く。持ち物が無ければ普通の遷移と同じ */
export function openWith(path: string, handoff?: Handoff): void {
  pending = handoff ? { path, handoff } : null;
  navigate(path);
  emit();
}

/** 自分宛の持ち物。他の画面へ渡されたものは見えない */
export function useHandoff(path: string): Handoff | null {
  const current = useSyncExternalStore(subscribe, getPending, () => null);
  return current && current.path === path ? current.handoff : null;
}
