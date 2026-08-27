/**
 * アシスタント（AgentPet）の居場所と表示。
 *
 * **消せるようにする。** 待ち時間に人の顔が一つあると安心する一方で、
 * 画面を歩き回るものが常にいると邪魔に感じる人もいる。デモ中に
 * 「消してください」と言われて消せないのは困る。
 *
 * **置き場所を覚える。** 掴んで動かしたということは「そこに居てほしい」
 * ということなので、次に開いたときに元の徘徊へ戻すのは裏切りになる。
 *
 * **既定の姿は画面ごと、選んだ姿は全画面で共有する。** 既定は AIチャットが虎、
 * ナレッジ登録がロボット。画面が違うことが姿で分かる。ただしこれは
 * 「まだ好みが無い人」のための既定であって、いったん選んだ後まで分け続ける
 * 理由は無い。片方で選んだ姿がもう片方に効かないと、同じ操作を2回させられる。
 *
 * **状態をモジュールに1つだけ置く。** view.ts / router.ts と同じ理由。
 * ヘッダーの「呼び戻す」ボタンと本体が別々に覚えると必ず食い違う。
 */

import { useSyncExternalStore } from "react";

/** 掴んで置いた場所。null なら目印を追いかける */
export type PetSpot = { x: number; y: number } | null;

/** アシスタントの姿 */
export type PetSkin = "tiger" | "robot";

/** アシスタントが住んでいる画面。姿と行き先はここで分かれる */
export type PetScene = "chat" | "ingest";

/**
 * 画面ごとの既定の姿。
 *
 * AIチャットは虎（プロダクト名がAI虎の巻なので）、ナレッジ登録はロボット。
 */
const DEFAULT_SKIN: Record<PetScene, PetSkin> = {
  chat: "tiger",
  ingest: "robot",
};

const HIDDEN_KEY = "torano-maki:pet:hidden";
const SPOT_KEY = "torano-maki:pet:spot";
const SKIN_KEY = "torano-maki:pet:skin";

function loadHidden(): boolean {
  try {
    return localStorage.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

function loadSpot(): PetSpot {
  try {
    const raw = localStorage.getItem(SPOT_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const { x, y } = parsed as { x?: unknown; y?: unknown };
    if (typeof x !== "number" || typeof y !== "number") return null;
    return { x, y };
  } catch {
    // 壊れた値で画面が落ちる方が困る。徘徊に戻せばよい
    return null;
  }
}

/** null は「まだ選んでいない」。画面ごとの既定で出す合図になる */
function loadChosenSkin(): PetSkin | null {
  try {
    const saved = localStorage.getItem(SKIN_KEY);
    if (saved === "robot" || saved === "tiger") return saved;
  } catch {
    // 読めなくても既定の姿で出せばよい
  }
  return null;
}

let hidden = typeof window === "undefined" ? false : loadHidden();
let spot: PetSpot = typeof window === "undefined" ? null : loadSpot();
let chosenSkin: PetSkin | null = typeof window === "undefined" ? null : loadChosenSkin();

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

function getHidden(): boolean {
  return hidden;
}


/** `spot` は差し替えでしか変わらない。同じ参照を返し続けるので再描画が暴れない */
function getSpot(): PetSpot {
  return spot;
}

function remember(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // 覚えられなくてもその場の操作は効く
  }
}

export function usePetHidden(): boolean {
  return useSyncExternalStore(subscribe, getHidden, () => false);
}

export function setPetHidden(next: boolean): void {
  if (hidden === next) return;
  hidden = next;
  remember(HIDDEN_KEY, next ? "1" : null);
  emit();
}

export function usePetSpot(): PetSpot {
  return useSyncExternalStore(subscribe, getSpot, () => null);
}

/** null を渡すと、また目印を追いかけるようになる */
export function setPetSpot(next: PetSpot): void {
  spot = next;
  remember(SPOT_KEY, next === null ? null : JSON.stringify(next));
  emit();
}

/**
 * その画面に出す姿。選ばれていればそれ、まだなら画面ごとの既定。
 *
 * 返すのは文字列なので、描画のたびに新しい値を作ることにはならない
 * （`useSyncExternalStore` は同じ値を返し続ける必要がある）。
 */
export function usePetSkin(scene: PetScene): PetSkin {
  return useSyncExternalStore(
    subscribe,
    () => chosenSkin ?? DEFAULT_SKIN[scene],
    () => DEFAULT_SKIN[scene],
  );
}

/** 選んだ姿は全画面で共有する。画面を跨ぐたびに選び直させない */
export function setPetSkin(next: PetSkin): void {
  if (chosenSkin === next) return;
  chosenSkin = next;
  remember(SKIN_KEY, next);
  emit();
}
