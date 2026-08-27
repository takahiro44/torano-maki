/**
 * AIが参照したナレッジ。
 *
 * **「回答の引用元」と書かないこと。** 検索は当たったが回答が「該当なし」に
 * なる場合も入る。どれを引用したかはモデルにしか分からず、書かせると捏造する
 * （backend/app/models/chat.py の Citation を参照）。
 * 引用元と書くと、回答と食い違ったときに矛盾して見える。
 *
 * 中身は必要になったときだけ取りに行く。出典は毎回5件つくため、
 * 開かれるか分からない詳細をまとめて読むとチャットの応答を邪魔する。
 *
 * **閉じている間は1件1行に畳む。** 出典は毎回5件つくので、1件ずつ枠を持つと
 * 回答本文より背が高くなり、次の質問へ進むのにスクロールが要る。読むのは
 * 回答であって出典の一覧ではないため、開くまでは索引の大きさに留める。
 */

import { useState } from "react";
import { getKnowledge } from "../../api/client";
import { navigate, roleplayStartPath } from "../../lib/router";
import type { Citation, Knowledge } from "../../types/api";
import { KnowledgeArticle } from "../KnowledgeArticle";
import { Spinner } from "./AgentTimeline";

const SPEAKER_LABEL: Record<string, string> = {
  salesperson: "営業",
  customer: "顧客",
  source: "原文",
  unknown: "不明",
};

/**
 * @param question この出典を得たときの利用者の質問。ロープレへ引き継ぐ。
 *   ナレッジIDだけ渡すと本人の問題意識が消えるため（lib/router.ts 参照）。
 */
export function Citations({ citations, question }: { citations: Citation[]; question: string }) {
  if (citations.length === 0) return null;

  return (
    <section className="space-y-1">
      <h3 className="text-[11px] font-medium text-slate-400">
        AIが参照したナレッジ
        <span className="ml-1 text-slate-300">{citations.length}件</span>
      </h3>
      <div className="divide-y divide-slate-100 overflow-hidden rounded-lg bg-white
                      ring-1 ring-slate-200/70">
        {citations.map((citation) => (
          <CitationCard
            key={citation.knowledge_id}
            citation={citation}
            question={question}
          />
        ))}
      </div>
    </section>
  );
}

function CitationCard({ citation, question }: { citation: Citation; question: string }) {
  const [open, setOpen] = useState(false);
  const [knowledge, setKnowledge] = useState<Knowledge | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || knowledge || loading) return;
    setLoading(true);
    setError(null);
    try {
      setKnowledge(await getKnowledge(citation.knowledge_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <button
        onClick={() => void toggle()}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-slate-50"
      >
        <Chevron open={open} />
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-slate-700">
          {citation.title}
        </span>
        <span className="shrink-0 text-[10px] text-slate-400">
          {citation.file_name ?? sourceLabel(citation.source_type)}
          {citation.utterances.length > 0 && ` · ${citation.utterances.length}発言`}
        </span>
      </button>

      {open && (
        <div className="space-y-2.5 border-t border-slate-100 px-2.5 py-2.5">
          {/* Agentが根拠の発話まで取りに行った場合だけ入る。
              追加のリクエストなしで出せるので先に見せる */}
          {citation.utterances.length > 0 && (
            <div className="space-y-1.5 border-l-2 border-indigo-200 pl-3">
              {citation.utterances.map((u) => (
                <p key={u.sequence_no} className="text-xs leading-relaxed text-slate-700">
                  <span className="mr-1 text-slate-400">
                    {SPEAKER_LABEL[u.speaker] ?? u.speaker}
                    {u.end_sec > 0.05 && ` ${u.start_sec.toFixed(0)}秒`}
                  </span>
                  {u.content}
                </p>
              ))}
            </div>
          )}

          {loading && (
            <p className="flex items-center gap-2 text-xs text-slate-500">
              <Spinner />
              読み込んでいます…
            </p>
          )}
          {error && <p className="text-xs text-rose-600">{error}</p>}
          {knowledge && <KnowledgeArticle knowledge={knowledge} />}
          <button
            onClick={() =>
              navigate(roleplayStartPath({ knowledgeId: citation.knowledge_id, query: question }))
            }
            className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-medium text-white
                       hover:bg-indigo-500"
          >
            この場面を練習する
          </button>
        </div>
      )}
    </div>
  );
}

function sourceLabel(sourceType: string | null): string {
  switch (sourceType) {
    case "audio":
      return "商談音声から抽出";
    case "document":
      return "文書から抽出";
    case "roleplay":
      return "ロープレから抽出";
    case "interview":
      return "ヒアリングから抽出";
    default:
      return "手入力";
  }
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 12 12"
      aria-hidden="true"
      className={
        "size-3 shrink-0 text-slate-300 transition-transform " + (open ? "rotate-90" : "")
      }
    >
      <path
        d="M4 2.5 8 6l-4 3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
