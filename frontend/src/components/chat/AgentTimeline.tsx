/**
 * AIが何を調べたかの実況。
 *
 * **畳まない。** 以前は `<details>` の中に隠していたが、「AIが本当にDBを見たのか」
 * を利用者が確認できることはこのプロダクトの価値そのものであり、
 * 既定で見えていないと信用する手がかりにならない。
 *
 * **実行前に出す。** 検索には数秒かかる。`tool_call` の時点で行を出しておき、
 * `tool_result` が届いたら結果を書き足す。完了してから出すと、
 * 一番待たされている間だけ画面が無言になる。
 */

import type { AgentStep } from "./useChat";

/**
 * 実況に出せる1行。
 *
 * **チャットの `AgentStep` より狭い。** ここが使うのは「何をしていて、
 * 終わったか、結果は何だったか」だけで、Tool名も引数も見ていない。
 * 型を狭く取っておくと、Toolを持たない工程（上司レビューの照合など）も
 * 同じ見た目で流せる。**見せ方を2つに分けないための型である。**
 */
export type TimelineStep = Pick<AgentStep, "step" | "label" | "summary" | "ok" | "errorCode">;

export function AgentTimeline({ steps, streaming }: { steps: TimelineStep[]; streaming: boolean }) {
  // 検索せずに答える質問（挨拶など）もある。何も無いときに空の枠を出さない
  if (steps.length === 0) {
    if (!streaming) return null;
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 text-sm text-slate-500"
      >
        <Spinner />
        考えています…
      </div>
    );
  }

  return (
    <ol role="status" aria-live="polite" className="space-y-2.5">
      {steps.map((step, i) => (
        <li key={step.step} className="relative flex gap-3">
          {/* 縦線。最後の行から下には伸ばさない */}
          {i < steps.length - 1 && (
            <span className="absolute top-5 bottom-[-14px] left-[7px] w-px bg-slate-200" />
          )}
          <StepIcon step={step} />
          <div className="min-w-0 flex-1 pt-px">
            <p className="text-sm text-slate-700">{step.summary ?? step.label}</p>
            {step.ok === false && step.errorCode && (
              <p className="mt-0.5 font-mono text-[11px] text-rose-500">{step.errorCode}</p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function StepIcon({ step }: { step: TimelineStep }) {
  if (step.ok === null) {
    return (
      <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center">
        <Spinner />
      </span>
    );
  }
  if (step.ok) {
    return (
      <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-indigo-50 ring-1 ring-indigo-200">
        <svg viewBox="0 0 12 12" className="size-2.5 text-indigo-600" aria-hidden="true">
          <path
            d="M2.5 6.2 4.8 8.5 9.5 3.8"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    );
  }
  return (
    <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-rose-50 ring-1 ring-rose-200">
      <svg viewBox="0 0 12 12" className="size-2.5 text-rose-600" aria-hidden="true">
        <path
          d="M3.5 3.5 8.5 8.5M8.5 3.5 3.5 8.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}

export function Spinner({ className = "size-4 text-slate-400" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={`animate-spin ${className}`} aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.2" />
      <path
        d="M8 2a6 6 0 0 1 6 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}
