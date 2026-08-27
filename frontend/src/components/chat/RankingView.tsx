/**
 * 検索の候補を順位で見せる。
 *
 * **AIが「見比べて選んだ」ことを数字で出す。** グラフは広がりを見せられるが、
 * どれがどれより近いのかは描けない。順位と帯なら、上位5件がAgentに渡り、
 * 6位以下は届かなかった、という線引きがそのまま見える。
 *
 * **RRFスコア（`score`）は出さない。** 0.016 のような値で差も僅かで、
 * 類似度ではなく順位を決めるための内部値のため、人が読む数字にならない
 * （backend/app/models/knowledge.py の KnowledgeSearchResult を参照）。
 * 出すのは順位と、意味の近さ（コサイン類似度）だけにする。
 */

import { useState } from "react";
import type { Candidate, CandidatesByStep } from "./candidates";
import { barWidth, cardPosition } from "./rankingVisuals";
import { ScenePopover, type SceneTarget } from "./ScenePopover";
import type { AgentStep, Turn } from "./useChat";

/** Agentに渡る件数。ここに線を引くと「選ばれた／選ばれなかった」が見える */
const HANDED_TO_AGENT = 5;

export function RankingView({
  turn,
  candidates,
}: {
  turn: Turn | null;
  candidates: CandidatesByStep;
}) {
  const [scene, setScene] = useState<SceneTarget | null>(null);

  if (!turn) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto bg-white px-3 py-4 text-[11px] leading-relaxed text-slate-400">
        質問すると、AIが何件の候補から選んだかがここに並びます。
      </div>
    );
  }

  const searches = turn.steps.filter((s) => candidates[s.step]?.length);

  return (
    <div className="relative min-h-0 flex-1 space-y-4 overflow-y-auto bg-white px-3 py-3">
      {turn.steps.length === 0 && (
        <p className="text-[11px] text-slate-400">
          {turn.status === "streaming"
            ? "どう調べるかを考えています…"
            : "検索せずに答えられる質問でした。"}
        </p>
      )}

      {turn.steps.map((step) =>
        candidates[step.step]?.length ? null : <PendingStep key={step.step} step={step} />,
      )}

      {searches.map((step, i) => (
        <SearchBlock
          key={step.step}
          step={step}
          candidates={candidates[step.step]}
          citedIds={new Set(turn.citations.map((c) => c.knowledge_id))}
          // 最後の検索の1位が、いま見てほしいところ
          focus={i === searches.length - 1}
          onOpen={setScene}
        />
      ))}

      {scene && (
        <ScenePopover
          key={scene.knowledgeId}
          target={scene}
          onClose={() => setScene(null)}
        />
      )}
    </div>
  );
}

/** 検索以外のTool、または候補がまだ返っていない検索 */
function PendingStep({ step }: { step: AgentStep }) {
  return (
    <p className="flex items-baseline gap-1.5 text-[11px] text-slate-500">
      <span className={step.ok === null ? "text-sky-500" : "text-indigo-500"}>
        {step.ok === null ? "▶" : "✓"}
      </span>
      <span className="min-w-0 flex-1 truncate">{step.summary ?? step.label}</span>
    </p>
  );
}

function SearchBlock({
  step,
  candidates,
  citedIds,
  focus,
  onOpen,
}: {
  step: AgentStep;
  candidates: Candidate[];
  citedIds: Set<string>;
  focus: boolean;
  onOpen: (target: SceneTarget) => void;
}) {
  const query = typeof step.args?.query === "string" ? step.args.query : step.label;
  const scores = candidates.map((c) => c.semanticScore ?? 0);
  const max = Math.max(...scores);
  const min = Math.min(...scores);

  return (
    <section className="space-y-1.5">
      <header className="space-y-0.5">
        <p className="truncate text-[11px] font-medium text-slate-700">🔍 {query}</p>
        <p className="text-[10px] text-slate-400">
          候補 {candidates.length}件 ／ 上位 {HANDED_TO_AGENT}件をAIに渡した
        </p>
      </header>

      <ol className="space-y-1">
        {candidates.map((candidate, i) => {
          const handed = i < HANDED_TO_AGENT;
          return (
            <li
              key={candidate.id}
              // 参照された1位の行が、アシスタントの行き先になる
              data-pet-anchor={focus && i === 0 ? "focus" : undefined}
              className="agent-rise"
              style={{ animationDelay: `${Math.min(i, 12) * 45}ms` }}
            >
              <button
                type="button"
                // **押せば会話が出る。** 類似度だけでは「なぜ上位なのか」が
                // 分からず、順位を信じる材料にならない
                onClick={(e) =>
                  onOpen({
                    knowledgeId: candidate.id,
                    title: candidate.title,
                    semanticScore: candidate.semanticScore,
                    cited: citedIds.has(candidate.id),
                    ...cardPosition(e.currentTarget.getBoundingClientRect()),
                  })
                }
                className={
                  "w-full rounded-md px-2 py-1.5 text-left transition-colors " +
                  (handed
                    ? "bg-indigo-50/80 ring-1 ring-indigo-200/70 hover:bg-indigo-100/70"
                    : "bg-slate-50 hover:bg-slate-100")
                }
              >
              <div className="flex items-baseline gap-1.5">
                <span
                  className={
                    "w-4 shrink-0 text-right font-mono text-[10px] " +
                    (handed ? "text-indigo-600" : "text-slate-400")
                  }
                >
                  {i + 1}
                </span>
                <span
                  className={
                    "min-w-0 flex-1 truncate text-[11.5px] " +
                    (handed ? "text-slate-800" : "text-slate-500")
                  }
                >
                  {candidate.title}
                </span>
                {citedIds.has(candidate.id) && (
                  <span className="shrink-0 text-[9px] font-medium text-indigo-600">参照</span>
                )}
              </div>

              <div className="mt-1 flex items-center gap-1.5">
                <div className="h-1 flex-1 overflow-hidden rounded-full bg-slate-200/70">
                  <div
                    className={
                      "h-full rounded-full transition-all duration-500 " +
                      (handed ? "bg-indigo-400" : "bg-slate-300")
                    }
                    style={{ width: barWidth(candidate.semanticScore, max, min) }}
                  />
                </div>
                <span className="shrink-0 font-mono text-[9px] text-slate-400">
                  {candidate.semanticScore?.toFixed(3) ?? "—"}
                </span>
                {/* 語の一致でも拾えた場合。ハイブリッド検索のもう一方の経路 */}
                {candidate.lexicalScore !== null && (
                  <span className="shrink-0 rounded bg-sky-100 px-1 text-[9px] text-sky-600">
                    語句
                  </span>
                )}
              </div>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
