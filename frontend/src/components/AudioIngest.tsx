/**
 * 「音声」タブの全体。アップロード → 文字起こしの確認 → ナレッジ化 → 承認。
 *
 * ナレッジ化には既存の /ingest/text をそのまま使う。音声専用の抽出APIを
 * 作らないのは、抽出の入力が結局テキストであり、経路を2本持つと
 * プロンプトやチャンク分割の改善を両方に当て続けることになるため。
 */

import { useState } from "react";
import { ingestText, updateKnowledge } from "../api/client";
import { formatClock } from "../lib/time";
import type { AudioTranscribeResponse, Knowledge } from "../types/api";
import { AudioInput } from "./AudioInput";
import { KnowledgeCard } from "./KnowledgeCard";

type Props = { onChanged: () => void };

export function AudioIngest({ onChanged }: Props) {
  const [result, setResult] = useState<AudioTranscribeResponse | null>(null);
  // 文字起こしは誤りを含む。抽出に回す前に直せるよう、編集可能な状態で持つ
  const [text, setText] = useState("");
  const [showSegments, setShowSegments] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState<Knowledge[]>([]);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  function accept(res: AudioTranscribeResponse) {
    setResult(res);
    setText(res.text);
    setPending([]);
    setMessage({
      kind: "ok",
      text: `${res.file_name} を文字起こししました（${formatClock(res.duration_sec)} / ${res.segments.length}区間 / ${res.text.length}文字）。内容を確認してからナレッジ化してください。`,
    });
  }

  async function extract() {
    const body = text.trim();
    if (!body || !result) return;
    setExtracting(true);
    setMessage(null);
    try {
      // data_source_id を渡すことで、抽出したナレッジがこの音声を出典として持つ
      const res = await ingestText(body, result.data_source_id);
      if (res.saved.length === 0) {
        setPending([]);
        setMessage({
          kind: "error",
          text: "ナレッジを抽出できませんでした。商談の経緯が含まれているか確認してください。",
        });
        return;
      }
      setPending(res.saved);
      const extra = res.notes?.length ? ` ${res.notes.join(" ")}` : "";
      setMessage({
        kind: "ok",
        text: `${res.saved.length}件を構造化しました。内容を確認して承認するとAIに聞く画面の検索・一覧の対象になります。${extra}`,
      });
      onChanged();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setExtracting(false);
    }
  }

  async function confirmIds(ids: string[]) {
    if (ids.length === 0) return;
    setConfirming(true);
    setMessage(null);
    try {
      const updated: Knowledge[] = [];
      for (const id of ids) {
        updated.push(await updateKnowledge(id, { status: "confirmed" }));
      }
      const confirmedIds = new Set(updated.map((k) => k.id));
      setPending((prev) => prev.filter((k) => !confirmedIds.has(k.id)));
      setMessage({
        kind: "ok",
        text: `${updated.length}件を承認しました。AIに聞く画面から検索できます。`,
      });
      onChanged();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setConfirming(false);
    }
  }

  const drafts = pending.filter((k) => k.status === "draft");
  const edited = result !== null && text !== result.text;

  return (
    <div className="space-y-5">
      <AudioInput onTranscribed={accept} />

      {message && (
        <p className={message.kind === "ok" ? "text-sm text-emerald-700" : "text-sm text-red-700"}>
          {message.text}
        </p>
      )}

      {result && (
        <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-800">文字起こし結果</h3>
            <button
              type="button"
              onClick={() => setShowSegments((v) => !v)}
              className="text-xs text-slate-500 underline underline-offset-2 hover:text-slate-800"
            >
              {showSegments ? "本文を編集する" : `時刻つきで見る（${result.segments.length}区間）`}
            </button>
          </div>

          {showSegments ? (
            // 編集した本文と時刻つき表示がずれるのを避けるため、閲覧専用にしている。
            // 時刻はDBのセグメントに紐づいており、本文を直しても変わらない
            <ul className="max-h-96 space-y-1 overflow-y-auto rounded-md bg-slate-50 p-3">
              {result.segments.map((s) => (
                <li key={s.sequence_no} className="flex gap-3 text-sm leading-relaxed">
                  <span className="shrink-0 font-mono text-xs text-slate-400">
                    {formatClock(s.start_sec)}
                  </span>
                  <span className="text-slate-700">{s.text}</span>
                </li>
              ))}
            </ul>
          ) : (
            <>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={12}
                className="w-full resize-y rounded-lg border border-slate-300 bg-white p-3 text-sm
                           leading-relaxed outline-none focus:border-slate-500 focus:ring-2
                           focus:ring-slate-200"
              />
              <p className="text-xs text-slate-400">
                {text.length}文字
                {edited && " ・ 修正済み"}
                {" ・ 聞き取りの誤りはここで直してからナレッジ化してください"}
              </p>
            </>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => void extract()}
              disabled={extracting || confirming || !text.trim()}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                         hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {extracting ? "抽出して登録中…" : "この内容でナレッジ化する"}
            </button>
            <span className="text-xs text-slate-400">長い商談では数分かかります</span>
          </div>
        </div>
      )}

      {drafts.length > 0 && (
        <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-800">抽出結果（下書き）</h3>
            <button
              type="button"
              onClick={() => void confirmIds(drafts.map((k) => k.id))}
              disabled={confirming}
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white
                         hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {confirming ? "承認中…" : "この内容で承認する"}
            </button>
          </div>
          <p className="text-xs text-slate-500">
            詳細を開くと、根拠になった発話を確認できます。承認しない場合は下書きのまま残ります。
          </p>
          {drafts.map((k) => (
            <KnowledgeCard
              key={k.id}
              knowledge={k}
              showEmptyDetails
              extra={
                <span className="rounded bg-white px-1.5 py-0.5 text-slate-600">{k.status}</span>
              }
              actions={
                <button
                  type="button"
                  onClick={() => void confirmIds([k.id])}
                  disabled={confirming}
                  className="text-slate-700 underline underline-offset-2 hover:text-slate-900"
                >
                  承認する
                </button>
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
