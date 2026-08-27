/**
 * 「その話、どの場面？」にその場で答える札。
 *
 * **順位と類似度だけでは納得できない。** 上位に来た理由は本文にあり、
 * 音声から起こしたナレッジなら、元の会話のどこを指しているかまで辿れる
 * （`GET /knowledge/{id}/evidence`）。一覧から離れずに読めることが大事で、
 * 別画面へ飛ばすと「探している最中」の流れが切れる。
 *
 * **開いてから取りに行く。** 候補は20件以上あり、全部の会話を先読みすると
 * 回答のストリーミングと帯域を取り合う。
 */

import { useEffect, useState } from "react";
import { getKnowledge, getKnowledgeEvidence } from "../../api/client";
import { navigate, roleplayStartPath } from "../../lib/router";
import type { Knowledge, KnowledgeEvidenceSpan } from "../../types/api";
import { KnowledgeArticle } from "../KnowledgeArticle";
import { Spinner } from "./AgentTimeline";

const SPEAKER_LABEL: Record<string, string> = {
  salesperson: "営業",
  customer: "顧客",
  source: "原文",
  unknown: "不明",
};

export type SceneTarget = {
  knowledgeId: string;
  title: string;
  semanticScore: number | null;
  cited: boolean;
  /** 押した行の位置。札はその高さに合わせて出す */
  top: number;
  left: number;
};

const CARD_WIDTH = 400;

function timeLabel(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function ScenePopover({ target, onClose }: { target: SceneTarget; onClose: () => void }) {
  const [spans, setSpans] = useState<KnowledgeEvidenceSpan[] | null>(null);
  const [knowledge, setKnowledge] = useState<Knowledge | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 別の行を押したときは呼び出し側が key で作り直すので、ここで初期化しない
  useEffect(() => {
    let alive = true;
    Promise.all([getKnowledgeEvidence(target.knowledgeId), getKnowledge(target.knowledgeId)])
      .then(([evidence, record]) => {
        if (!alive) return;
        setSpans(evidence);
        setKnowledge(record);
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [target.knowledgeId]);

  // Escapeで閉じる。読み終わったあと、閉じるボタンを探させない
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const utterances = (spans ?? []).flatMap((span) => span.utterances);

  return (
    <>
      {/* 外側を押したら閉じる。札の外に注意が移った時点で用は済んでいる */}
      <div className="fixed inset-0 z-30" onClick={onClose} aria-hidden="true" />

      <aside
        data-pet-anchor="popup"
        className="agent-rise fixed z-40 flex max-h-[70vh] flex-col overflow-hidden rounded-xl bg-white shadow-xl ring-1 ring-slate-200"
        style={{ top: target.top, left: target.left, width: CARD_WIDTH }}
      >
        <header className="flex items-start gap-2 border-b border-slate-100 px-3.5 py-2.5">
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium text-slate-800">{target.title}</p>
            <p className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-400">
              {target.semanticScore !== null && (
                <span className="font-mono">意味の近さ {target.semanticScore.toFixed(3)}</span>
              )}
              {target.cited && <span className="text-emerald-600">AIが参照</span>}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="閉じる"
            className="rounded px-1.5 text-slate-300 hover:bg-slate-100 hover:text-slate-600"
          >
            ✕
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3.5 py-3">
          {error && <p className="text-xs text-rose-600">{error}</p>}

          {spans === null && !error && (
            <p className="flex items-center gap-2 text-xs text-slate-500">
              <Spinner />
              会話を読み込んでいます…
            </p>
          )}

          {knowledge && (
            <section>
              <h4 className="mb-1.5 text-[10px] font-medium text-slate-400">構造化データ</h4>
              <KnowledgeArticle knowledge={knowledge} showEmpty />
            </section>
          )}

          {spans !== null && utterances.length === 0 && (
            <p className="rounded-lg bg-slate-50 px-3 py-2.5 text-xs leading-relaxed text-slate-500">
              このナレッジには会話の記録がありません。音声から取り込んだものだけ、
              元の商談のどこを指しているかを辿れます。
            </p>
          )}

          {utterances.length > 0 && (
            <section>
              <h4 className="text-[10px] font-medium text-slate-400">
                この記事のもとになった会話
                <span className="ml-1.5">{utterances.length}発言</span>
              </h4>
              <ol className="mt-1.5 space-y-2 border-l-2 border-indigo-200 pl-3">
                {utterances.map((u) => (
                  <li key={u.id} className="text-xs leading-relaxed">
                    <span
                      className={
                        "mr-1.5 font-medium " +
                        (u.speaker === "customer" ? "text-indigo-600" : "text-slate-400")
                      }
                    >
                      {SPEAKER_LABEL[u.speaker] ?? u.speaker}
                      {u.end_sec > 0.05 && (
                        <span className="ml-1 font-mono text-[10px] text-slate-300">
                          {timeLabel(u.start_sec)}
                        </span>
                      )}
                    </span>
                    <span className="text-slate-700">{u.content}</span>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>

        <footer className="shrink-0 border-t border-slate-100 px-3.5 py-2.5">
          <button
            onClick={() => navigate(roleplayStartPath({ knowledgeId: target.knowledgeId }))}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
          >
            この場面を練習する
          </button>
        </footer>
      </aside>
    </>
  );
}
