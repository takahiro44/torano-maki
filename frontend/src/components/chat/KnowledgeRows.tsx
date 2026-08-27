/**
 * サイドパネル共通の行リスト。RankingView の行と見た目を揃え、
 * 待機中の登録済み一覧・手動検索の結果の両方をこの形で出す。
 *
 * 「Agentに渡ったか（上位5件）」の区別はランキング固有の概念なので持たない
 * （手動検索は別の検索なので、そもそも「渡す」対象がない）。ただし
 * 「AIの回答が実際に参照したか」（cited）は turn.citations から分かるので、
 * ランキングで緑になっていたのと同じ項目は、ここでも緑のまま出す。
 */

import { barWidth, cardPosition } from "./rankingVisuals";
import type { SceneTarget } from "./ScenePopover";

export type KnowledgeRowItem = { id: string; title: string; score: number | null; cited?: boolean };

export function KnowledgeRows({
  items,
  emptyLabel,
  onOpen,
}: {
  items: KnowledgeRowItem[];
  emptyLabel: string;
  onOpen: (target: SceneTarget) => void;
}) {
  if (items.length === 0) {
    return <p className="px-1 text-[11px] text-slate-400">{emptyLabel}</p>;
  }

  const scores = items.map((i) => i.score).filter((s): s is number => s !== null);
  const max = scores.length ? Math.max(...scores) : 0;
  const min = scores.length ? Math.min(...scores) : 0;

  return (
    <ol className="space-y-1">
      {items.map((item, i) => {
        const cited = item.cited ?? false;
        return (
          <li key={item.id}>
            <button
              type="button"
              onClick={(e) =>
                onOpen({
                  knowledgeId: item.id,
                  title: item.title,
                  semanticScore: item.score,
                  cited,
                  ...cardPosition(e.currentTarget.getBoundingClientRect()),
                })
              }
              className={
                "w-full rounded-md px-2 py-1.5 text-left transition-colors " +
                (cited
                  ? "bg-emerald-50/70 ring-1 ring-emerald-200/70 hover:bg-emerald-100/70"
                  : "bg-slate-50 hover:bg-slate-100")
              }
            >
              <div className="flex items-baseline gap-1.5">
                <span
                  className={
                    "w-4 shrink-0 text-right font-mono text-[10px] " +
                    (cited ? "text-emerald-600" : "text-slate-400")
                  }
                >
                  {i + 1}
                </span>
                <span
                  className={
                    "min-w-0 flex-1 truncate text-[11.5px] " +
                    (cited ? "text-slate-800" : "text-slate-700")
                  }
                >
                  {item.title}
                </span>
                {cited && (
                  <span className="shrink-0 text-[9px] font-medium text-emerald-600">参照</span>
                )}
              </div>
              {item.score !== null && (
                <div className="mt-1 flex items-center gap-1.5 pl-[22px]">
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-slate-200/70">
                    <div
                      className={"h-full rounded-full " + (cited ? "bg-emerald-400" : "bg-slate-300")}
                      style={{ width: barWidth(item.score, max, min) }}
                    />
                  </div>
                  <span className="shrink-0 font-mono text-[9px] text-slate-400">
                    {item.score.toFixed(3)}
                  </span>
                </div>
              )}
            </button>
          </li>
        );
      })}
    </ol>
  );
}
