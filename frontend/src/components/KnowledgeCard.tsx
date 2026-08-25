/**
 * 一覧・検索のカード。タイトルと要約だけ出し、詳細と根拠原文は開いたとき。
 */

import { useEffect, useState, type ReactNode } from "react";
import { getKnowledgeEvidence } from "../api/client";
import type { Knowledge, KnowledgeEvidenceSpan } from "../types/api";
import { KnowledgeArticle } from "./KnowledgeArticle";

type Props = {
  knowledge: Knowledge;
  rank?: number;
  extra?: ReactNode;
  actions?: ReactNode;
  showEmptyDetails?: boolean;
};

const SPEAKER_LABEL: Record<string, string> = {
  salesperson: "営業",
  customer: "顧客",
  source: "原文",
  unknown: "不明",
};

function knowledgeSummary(k: Knowledge): string {
  const parts = [k.lesson, k.situation, k.problem, k.action];
  for (const p of parts) {
    const t = (p ?? "").trim();
    if (t) return t;
  }
  return "要約はまだありません";
}

function speakerLabel(speaker: string): string {
  return SPEAKER_LABEL[speaker] ?? speaker;
}

export function KnowledgeCard({
  knowledge,
  rank,
  extra,
  actions,
  showEmptyDetails = false,
}: Props) {
  const [open, setOpen] = useState(false);
  const [spans, setSpans] = useState<KnowledgeEvidenceSpan[] | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const rows = await getKnowledgeEvidence(knowledge.id);
        if (cancelled) return;
        setSpans(rows);
        setEvidenceError(null);
      } catch (e) {
        if (cancelled) return;
        setEvidenceError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, knowledge.id]);

  const summary = knowledgeSummary(knowledge);
  const meta = [knowledge.industry, knowledge.product, knowledge.sales_stage].filter(
    (v): v is string => Boolean(v && v.trim()),
  );

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start gap-3">
        {rank !== undefined && (
          <span className="mt-0.5 shrink-0 rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
            {rank}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-semibold leading-snug text-slate-900">{knowledge.title}</h3>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-slate-600">{summary}</p>
          {meta.length > 0 && (
            <p className="mt-1 text-xs text-slate-400">{meta.join(" · ")}</p>
          )}
        </div>
      </div>

      <div className="mt-3">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="rounded-md border border-slate-300 bg-white px-3 py-1 text-sm text-slate-700 hover:bg-slate-50"
        >
          {open ? "詳細を閉じる" : "詳細を見る"}
        </button>
      </div>

      {open && (
        <div className="mt-4 space-y-4 border-t border-slate-100 pt-4">
          <section>
            <h4 className="mb-2 text-xs font-medium tracking-wide text-slate-500">構造化データ</h4>
            <KnowledgeArticle knowledge={knowledge} showEmpty={showEmptyDetails} />
          </section>
          <section>
            <h4 className="mb-2 text-xs font-medium tracking-wide text-slate-500">根拠の生文</h4>
            {evidenceError && <p className="text-sm text-red-700">{evidenceError}</p>}
            {spans === null && !evidenceError && (
              <p className="text-sm text-slate-400">読み込み中…</p>
            )}
            {spans && spans.length === 0 && (
              <p className="text-sm text-slate-400">
                根拠発話はまだ紐づいていません。テキスト登録のうち、抽出時に原文を残したものだけ表示できます。
              </p>
            )}
            {spans &&
              spans.map((span, i) => (
                <div key={`${span.start_sequence_no}-${i}`} className="space-y-2">
                  {spans.length > 1 && (
                    <p className="text-[11px] text-slate-400">
                      発話 {span.start_sequence_no}–{span.end_sequence_no}
                    </p>
                  )}
                  {span.utterances.map((u) => (
                    <figure
                      key={u.id}
                      className="rounded-md bg-slate-50 px-3 py-2"
                    >
                      <figcaption className="text-[11px] text-slate-500">
                        {speakerLabel(u.speaker)}
                        {u.end_sec > 0.05
                          ? ` · ${u.start_sec.toFixed(1)}–${u.end_sec.toFixed(1)}秒`
                          : ""}
                      </figcaption>
                      <blockquote className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                        {u.content}
                      </blockquote>
                    </figure>
                  ))}
                </div>
              ))}
          </section>
        </div>
      )}

      {(extra || actions) && (
        <div className="mt-3 flex items-center gap-3 border-t border-slate-100 pt-2 text-xs text-slate-400">
          {extra}
          {actions && <div className="ml-auto flex gap-2">{actions}</div>}
        </div>
      )}
    </div>
  );
}
