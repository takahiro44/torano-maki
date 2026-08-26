/**
 * 秒数の表示。
 *
 * コンポーネントから切り出しているのは、複数のコンポーネントで使うため。
 * コンポーネントのファイルから関数を export すると
 * Fast Refresh が効かなくなる（oxlint の react/only-export-components）。
 */

/** 秒を m:ss にする。音声の位置を指すのに使う */
export function formatClock(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
