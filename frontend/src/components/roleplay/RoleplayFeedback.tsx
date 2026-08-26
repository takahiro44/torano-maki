/**
 * 振り返り。
 *
 * **総合点を主役にしない。** 「共感力82点」のような根拠のない数値は出さず、
 * 場面ごとに決めた観点（rubric）の達成度と、社内の実際の発話を並べる。
 *
 * **使えない場面（limitations）を必ず出す。** 成功例の模倣だけを正解にすると、
 * 後輩が場面を選ばずに真似てしまう。
 *
 * **元の発話と時刻を出す。** 「AIがそう言った」ではなく
 * 「実際にこう言った人がいる」まで辿れることが、この機能の価値そのもの。
 */

import type { ReferencedKnowledge, RoleplayFeedback, RubricVerdict } from "../../types/api";
import { formatClock } from "../../lib/time";

type Props = {
  feedback: RoleplayFeedback;
  references: ReferencedKnowledge[];
  onRetry: () => void;
  onNewScene: () => void;
  retrying: boolean;
};

const VERDICT_STYLE: Record<RubricVerdict, { label: string; className: string }> = {
  met: { label: "できていた", className: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  partial: { label: "あと一歩", className: "bg-amber-50 text-amber-900 border-amber-200" },
  not_met: { label: "できていない", className: "bg-rose-50 text-rose-800 border-rose-200" },
};

/** 話者ラベル。現状のSTT経路は話者分離をしないため unknown が多い */
const SPEAKER_LABEL: Record<string, string> = {
  salesperson: "営業",
  customer: "顧客",
  source: "原文",
  unknown: "話者不明",
};

export function RoleplayFeedbackView({
  feedback,
  references,
  onRetry,
  onNewScene,
  retrying,
}: Props) {
  return (
    <div className="space-y-5">
      <section>
        <h2 className="text-lg font-semibold">振り返り</h2>
        <div className="mt-2 space-y-2">
          {feedback.rubric_results.map((result) => {
            const style = VERDICT_STYLE[result.verdict];
            return (
              <div key={result.key} className={`rounded-lg border p-3 ${style.className}`}>
                <div className="flex items-baseline justify-between gap-2">
                  <p className="text-sm font-medium">{result.label}</p>
                  <span className="shrink-0 text-xs font-medium">{style.label}</span>
                </div>
                <p className="mt-1 text-xs leading-relaxed">{result.comment}</p>
              </div>
            );
          })}
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <p className="text-xs font-medium text-slate-500">次に試す一言</p>
        <p className="mt-1 text-sm font-medium text-slate-900">「{feedback.next_phrase}」</p>
        <p className="mt-3 text-xs text-slate-500">
          もう一度やるなら: <span className="text-slate-800">{feedback.focus_next_try}</span>
        </p>
      </section>

      {feedback.strengths.length > 0 && (
        <PointList title="できていた点" items={feedback.strengths} />
      )}
      {feedback.improvements.length > 0 && (
        <PointList title="次はこうする" items={feedback.improvements} />
      )}

      <section>
        <h3 className="text-sm font-medium text-slate-700">参照した社内事例</h3>
        <p className="mt-0.5 text-xs text-slate-400">
          この練習で実際に使われたナレッジです。AIが挙げたものではありません。
        </p>
        <div className="mt-2 space-y-3">
          {references.map((ref) => (
            <ReferenceCard key={ref.knowledge_id} reference={ref} />
          ))}
          {references.length === 0 && (
            <p className="text-xs text-slate-400">参照した事例を取得できませんでした。</p>
          )}
        </div>
      </section>

      <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-4">
        <button
          onClick={onRetry}
          disabled={retrying}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                     hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {retrying ? "準備しています…" : "同じ場面でもう一度"}
        </button>
        <button
          onClick={onNewScene}
          className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700
                     hover:border-slate-500"
        >
          別の場面へ
        </button>
      </div>
    </div>
  );
}

function PointList({ title, items }: { title: string; items: string[] }) {
  return (
    <section>
      <h3 className="text-sm font-medium text-slate-700">{title}</h3>
      <ul className="mt-1 space-y-1">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 text-sm text-slate-700">
            <span className="text-slate-300">・</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ReferenceCard({ reference }: { reference: ReferencedKnowledge }) {
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="text-sm font-medium text-slate-900">{reference.title}</h4>
        {reference.usage_type === "primary" && (
          <span className="shrink-0 rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-700">
            この場面のもと
          </span>
        )}
      </div>
      <p className="mt-0.5 text-[11px] text-slate-400">
        出典: {reference.file_name ?? "手入力"}
      </p>

      {reference.utterances.length > 0 && (
        <div className="mt-2 space-y-1 border-l-2 border-slate-200 pl-3">
          {reference.utterances.map((u) => (
            <p key={u.sequence_no} className="text-xs leading-relaxed text-slate-700">
              <span className="text-slate-400">
                {SPEAKER_LABEL[u.speaker] ?? u.speaker}
                {/* 合成セグメントは時刻を持たないので、意味のあるときだけ出す */}
                {u.end_sec > 0.05 && ` ${formatClock(u.start_sec)}`}
              </span>{" "}
              {u.content}
            </p>
          ))}
        </div>
      )}

      {reference.applicable_situations && (
        <p className="mt-2 text-xs text-slate-600">
          <span className="font-medium text-slate-500">使える場面: </span>
          {reference.applicable_situations}
        </p>
      )}
      {reference.limitations && (
        <p className="mt-1 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-900">
          <span className="font-medium">この条件では使えない: </span>
          {reference.limitations}
        </p>
      )}
    </article>
  );
}
