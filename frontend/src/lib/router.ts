/**
 * 最小のルーター。
 *
 * **なぜ入れるか。** ロープレのセッションは数分続き、`session_id` を持つ。
 * URLが無いと、再読込で練習が消え、デモ中に画面が固まったとき復帰できず、
 * 「もう一度挑戦」で作った新しいセッションにも戻れない。
 * バックエンドに `GET /roleplay/sessions/{id}` を用意したのは、
 * URLから復帰できることを前提にしているため。
 *
 * **なぜライブラリを使わないか。** 必要なのは数画面と
 * `/roleplay/sessions/:sessionId` の1つだけで、ネストしたルートも
 * ローダーも要らない。依存を増やさない方針（CLAUDE.md 3.1）に沿って、
 * History API を薄く包むだけにしている。
 * ネストや遅延読み込みが必要になったら react-router へ移せばよい
 * （そのときは CLAUDE.md 3章どおりチームの合意を取ること）。
 *
 * **状態をモジュールに1つだけ持つ。** `useState` を各コンポーネントに
 * 持たせると、子が遷移しても親が古いパスのままになり、URLだけ変わって
 * 画面が変わらない。購読側が何個あっても同じ値を見るようにする。
 *
 * **アンマウントの扱いは呼び出し側の責任。** このフックはパスを返すだけで、
 * 何を描くかは決めない。チャットのように「画面を離れても状態を保つ」ものが
 * あるため、ここで一律にアンマウントさせてはいけない。
 */

import { useSyncExternalStore } from "react";

/** パスの正規化。末尾スラッシュの有無で別ルート扱いにしない */
function normalize(route: string): string {
  const queryStart = route.indexOf("?");
  const path = queryStart === -1 ? route : route.slice(0, queryStart);
  const search = queryStart === -1 ? "" : route.slice(queryStart);
  const normalizedPath = path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path || "/";
  return normalizedPath + search;
}

function browserRoute(): string {
  return normalize(window.location.pathname + window.location.search);
}

const listeners = new Set<() => void>();

let currentPath = typeof window === "undefined" ? "/" : browserRoute();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  return () => {
    listeners.delete(onStoreChange);
  };
}

function getSnapshot(): string {
  return currentPath;
}

// テストのSSRレンダリングでは window が無い。購読自体を張らない
if (typeof window !== "undefined") {
  // 戻る・進むはブラウザ側で起きるため、popstate を拾わないと
  // URLだけ変わって画面が変わらない状態になる
  window.addEventListener("popstate", () => {
    currentPath = browserRoute();
    emit();
  });
}

/**
 * 画面を切り替える。
 *
 * `replace` は「置き換え」。セッション作成のように、戻るボタンで
 * 前の状態（作成前のフォーム）へ戻っても意味がない遷移で使う。
 *
 * フックの外でも呼べるようにしているのは、API応答後のコールバックから
 * 遷移することが多いため。
 */
export function navigate(to: string, options?: { replace?: boolean }): void {
  const next = normalize(to);
  if (next === currentPath) return;
  if (options?.replace) window.history.replaceState(null, "", next);
  else window.history.pushState(null, "", next);
  currentPath = next;
  emit();
}

/** 現在のパス。変わったら再描画される */
export function useRoutePath(): string {
  return useSyncExternalStore(subscribe, getSnapshot, () => "/");
}

export const ROLEPLAY_PATH = "/roleplay";

export function roleplayStartPath(knowledgeId?: string): string {
  if (!knowledgeId) return ROLEPLAY_PATH;
  return `${ROLEPLAY_PATH}?knowledge_id=${encodeURIComponent(knowledgeId)}`;
}

export function readKnowledgeIdParam(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("knowledge_id");
}

export function roleplaySessionPath(sessionId: string): string {
  return `${ROLEPLAY_PATH}/sessions/${sessionId}`;
}

/**
 * `/roleplay/sessions/:sessionId` からIDを取り出す。該当しなければ null。
 *
 * 正規表現を画面側に散らかさないためにここへ置く。URLの形を変えるときに
 * 直す場所を1つにしておかないと、片方だけ直して静かに壊れる。
 */
export function matchRoleplaySession(path: string): string | null {
  const pathname = normalize(path).split("?", 1)[0];
  const matched = /^\/roleplay\/sessions\/([0-9a-fA-F-]{36})$/.exec(pathname);
  return matched ? matched[1] : null;
}
