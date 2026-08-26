/**
 * AIが探している最中を見せる作業パネル。
 *
 * **会話の隣に置く。** 調査の経過を会話の中に全部積むと、質問と回答の間が
 * 何十行も空いて読めなくなる。かといって畳むと「本当にDBを見たのか」を
 * 確かめられない（AgentTimeline の判断と同じ）。別の面に出すことで、
 * 経過を見せたまま会話は会話として読める。
 *
 * **走査中に流すのは実在のナレッジ。** それらしい文字列を流すのは
 * 演出ではなく嘘になる。検索対象は confirmed のナレッジ全件なので
 * （backend/app/services/search.py）、その一覧を実際に流している。
 */

import { useEffect, useState } from "react";
import { listKnowledge } from "../../api/client";
import { useCandidates } from "./candidates";
import { currentPhase, type Phase } from "./phase";
import { RankingView } from "./RankingView";
import type { Turn } from "./useChat";

/** 走査中に流す行の入れ替え間隔（ミリ秒）。速すぎると読めず、遅いと止まって見える */
const SCAN_INTERVAL_MS = 70;
const SCAN_ROWS = 5;

/**
 * 星図と走査に出すナレッジの上限。
 *
 * **検索対象の全件を出す。** 一部だけを出して「N件を検索中」と書くと、
 * 画面の点の数と件数が合わず、どちらかが嘘になる。APIの上限が200件なので
 * そこまでは実物をそのまま出す。
 */
const CORPUS_LIMIT = 200;

type CorpusItem = { id: string; title: string };

export function AgentWorkspace({ turn, onClose }: { turn: Turn | null; onClose: () => void }) {
  const [corpus, setCorpus] = useState<CorpusItem[]>([]);

  useEffect(() => {
    // 失敗しても会話には影響しない。星図と走査表示が出ないだけ
    listKnowledge({ status: "confirmed", limit: CORPUS_LIMIT })
      .then((items) => setCorpus(items.map((k) => ({ id: k.id, title: k.title }))))
      .catch(() => setCorpus([]));
  }, []);

  const candidates = useCandidates(turn);
  const phase = currentPhase(turn);

  return (
    <aside className="flex h-full flex-col border-r border-slate-200/80 bg-slate-50">
      <header className="flex shrink-0 items-center gap-2 border-b border-slate-200/80 bg-white px-3 py-2.5">
        <StatusPill phase={phase} />
        <span className="min-w-0 flex-1 truncate text-xs text-slate-400">
          {turn ? turn.question : `検索対象 ${corpus.length}件`}
        </span>
        <button
          onClick={onClose}
          aria-label="調査ビューを閉じる"
          className="rounded px-1.5 py-0.5 text-slate-300 hover:bg-slate-100 hover:text-slate-600"
        >
          ✕
        </button>
      </header>

      <RankingView turn={turn} candidates={candidates} />

      {(phase === "planning" || phase === "searching") && (
        <ScanTicker corpus={corpus} phase={phase} />
      )}
    </aside>
  );
}

const PHASE_PILL: Record<Phase, [string, string]> = {
  idle: ["待機中", "bg-slate-100 text-slate-500 ring-slate-200"],
  planning: ["思考中", "bg-amber-50 text-amber-700 ring-amber-200"],
  searching: ["調査中", "bg-amber-50 text-amber-700 ring-amber-200"],
  answering: ["回答中", "bg-indigo-50 text-indigo-700 ring-indigo-200"],
  done: ["完了", "bg-emerald-50 text-emerald-700 ring-emerald-200"],
  error: ["失敗", "bg-rose-50 text-rose-700 ring-rose-200"],
};

function StatusPill({ phase }: { phase: Phase }) {
  const [label, tone] = PHASE_PILL[phase];

  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${tone}`}
      role="status"
      aria-live="polite"
    >
      {label}
    </span>
  );
}

/**
 * 走査中に流れる行。
 *
 * **待たされている数秒を無言にしない。** ベクトル検索は蓄積ナレッジ全件を
 * 相手にしているので、その全件が流れていくこと自体が「今どこを見ているか」の
 * 説明になる。件数だけを出すよりも、規模が体で分かる。
 */
function ScanTicker({ corpus, phase }: { corpus: CorpusItem[]; phase: Phase }) {
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    if (corpus.length === 0) return;
    const timer = window.setInterval(() => setOffset((n) => n + 1), SCAN_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [corpus.length]);

  if (corpus.length === 0) return null;

  const rows = Array.from(
    { length: Math.min(SCAN_ROWS, corpus.length) },
    (_, i) => corpus[(offset + i) % corpus.length],
  );

  return (
    <div className="shrink-0 border-t border-slate-200/80 bg-white px-3 py-2">
      <p className="mb-1 flex items-center gap-1.5 text-[10px] font-medium tracking-wide text-amber-600">
        <span className="inline-block size-1.5 animate-pulse rounded-full bg-amber-400" />
        {phase === "searching"
          ? `SCANNING · ${corpus.length}件を照合中`
          : "PLANNING · 検索の条件を組み立て中"}
      </p>
      <ol className="space-y-0.5 font-mono text-[10px] leading-tight">
        {rows.map((item, i) => (
          <li
            key={`${offset}-${item.id}`}
            className="truncate text-slate-400"
            style={{ opacity: 1 - i * 0.18 }}
          >
            <span className="text-indigo-400">{item.id.slice(0, 8)}</span> {item.title}
          </li>
        ))}
      </ol>
    </div>
  );
}
