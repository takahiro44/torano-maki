/**
 * 回答の下に出る「次にできること」。
 *
 * **最新のターンにだけ出す。** 過去の回答すべてにボタンが並ぶと、
 * 会話を読み返すときに邪魔になるうえ、どれが今の話なのか分からなくなる。
 *
 * **出典カードの中に埋めない。** 以前は「この場面を練習する」が出典を
 * 開かないと現れなかった。会話から他の機能へ移る導線は、開かなくても
 * 見えている位置に無いと使われない。
 */

import { nextActions } from "./actions";
import type { Turn } from "./useChat";

export function NextActions({ turn, onReview }: { turn: Turn; onReview: () => void }) {
  const actions = nextActions(turn, onReview);
  if (actions.length === 0) return null;

  return (
    <section className="space-y-2">
      <h3 className="text-xs font-medium text-slate-500">次にできること</h3>
      <div className="flex flex-wrap gap-2">
        {actions.map((action) => (
          <button
            key={action.key}
            onClick={action.run}
            className={
              "group rounded-xl px-3.5 py-2 text-left transition-colors " +
              (action.tone === "primary"
                ? "bg-indigo-600 text-white hover:bg-indigo-500"
                : "bg-white text-slate-700 ring-1 ring-slate-200/80 hover:bg-slate-50 hover:ring-slate-300")
            }
          >
            <span className="block text-sm font-medium">{action.label}</span>
            <span
              className={
                "mt-0.5 block max-w-[16rem] truncate text-[11px] " +
                (action.tone === "primary" ? "text-indigo-100" : "text-slate-400")
              }
            >
              {action.hint}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
