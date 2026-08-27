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
 *
 * **検索は常に出す。旧「探す」「一覧」タブはここに吸収した。** 質問する前は
 * ランキングの代わりに登録済み一覧を、質問した後もランキングの見た目は
 * そのままに、検索欄だけは残す（検索結果はランキングの上に別枠で出す）。
 * どちらの行リストも RankingView と同じ見た目（KnowledgeRows）にして、
 * クリックすると ScenePopover で全文を読める。
 */

import { useEffect, useState } from "react";
import { listKnowledge, searchKnowledge } from "../../api/client";
import type { KnowledgeSearchResult } from "../../types/api";
import { useCandidates } from "./candidates";
import { KnowledgeRows } from "./KnowledgeRows";
import { currentPhase, type Phase } from "./phase";
import { RankingView } from "./RankingView";
import { ScenePopover, type SceneTarget } from "./ScenePopover";
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

/**
 * 手動検索で取る件数。
 *
 * **バックエンドの上限まで出す。** `/search` の `top_k` は
 * `ge=1, le=50`（backend/app/models/knowledge.py）で頭打ちのため、
 * 「全件」は技術的に50件が上限になる。
 */
const SEARCH_TOP_K = 50;

type CorpusItem = { id: string; title: string };

type Props = {
  turn: Turn | null;
  onClose: () => void;
  /** 待機中の一覧の再取得トリガー。登録・音声タブでの登録時にApp側でbumpされる */
  reloadKey: number;
};

export function AgentWorkspace({ turn, onClose, reloadKey }: Props) {
  const [corpus, setCorpus] = useState<CorpusItem[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [scene, setScene] = useState<SceneTarget | null>(null);

  useEffect(() => {
    // 失敗しても会話には影響しない。星図と走査表示・待機中の一覧が出ないだけ
    listKnowledge({ status: "confirmed", limit: CORPUS_LIMIT })
      .then((items) => setCorpus(items.map((k) => ({ id: k.id, title: k.title }))))
      .catch(() => setCorpus([]));
  }, [reloadKey]);

  async function runSearch(q: string) {
    const text = q.trim();
    if (!text) {
      setResults(null);
      setSearchError(null);
      return;
    }
    setSearching(true);
    try {
      setResults(await searchKnowledge(text, SEARCH_TOP_K));
      setSearchError(null);
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : String(e));
      setResults(null);
    } finally {
      setSearching(false);
    }
  }

  function clearSearch() {
    setQuery("");
    setResults(null);
    setSearchError(null);
  }

  const candidates = useCandidates(turn);
  const phase = currentPhase(turn);
  // 検索結果のうち、AIの回答が実際に参照したものはランキングと同じ緑にする
  const citedIds = new Set(turn?.citations.map((c) => c.knowledge_id) ?? []);

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

      {/* 検索は待機中・調査中を問わず常に出す */}
      <form
        className="flex shrink-0 items-center gap-1.5 border-b border-slate-200/80 bg-white px-3 py-2"
        onSubmit={(e) => {
          e.preventDefault();
          void runSearch(query);
        }}
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="ナレッジを探す"
          className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs
                     outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
        />
        {query && (
          <button
            type="button"
            onClick={clearSearch}
            className="shrink-0 rounded px-1 text-slate-300 hover:bg-slate-100 hover:text-slate-600"
            aria-label="検索をやめる"
          >
            ✕
          </button>
        )}
        <button
          type="submit"
          disabled={searching || !query.trim()}
          className="shrink-0 rounded-md bg-slate-900 px-2.5 py-1 text-xs font-medium text-white
                     hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {searching ? "…" : "検索"}
        </button>
      </form>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto bg-white px-3 py-3">
        {searchError && <p className="text-[11px] text-rose-600">{searchError}</p>}

        {results !== null && (
          <section>
            <p className="mb-1.5 text-[10px] text-slate-400">
              「{query}」の関連順・{results.length}件
            </p>
            <KnowledgeRows
              items={results.map((r) => ({
                id: r.id,
                title: r.title,
                score: r.semantic_score,
                cited: citedIds.has(r.id),
              }))}
              emptyLabel="該当が見つかりませんでした。"
              onOpen={setScene}
            />
          </section>
        )}

        {turn ? (
          // ランキングの見た目はそのまま。検索結果は上の枠に別で出す
          <RankingView turn={turn} candidates={candidates} />
        ) : (
          results === null && (
            <KnowledgeRows
              items={corpus.map((c) => ({ id: c.id, title: c.title, score: null }))}
              emptyLabel="まだ登録済みのナレッジがありません。"
              onOpen={setScene}
            />
          )
        )}
      </div>

      {scene && <ScenePopover key={scene.knowledgeId} target={scene} onClose={() => setScene(null)} />}

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
