/**
 * AIチャットの会話を上司に確認してもらうためのパネル。
 *
 * 「まとめる」と「上司に送信」を分けているのは、要約だけ見て
 * 送らない選択肢を残すため。送信時はクライアントが計算した要約を
 * 信用せず、サーバ側で再計算する（chat.pyの「Agentの自己申告を
 * 信用しない」設計思想と合わせる）。
 *
 * **開始のボタンは持たない。** 会話から他機能へ移る導線は
 * NextActions に集めてある。ここに独自のボタンを残すと、同じ操作の
 * 入口が2つになり、どちらが正しいか分からなくなる。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { sendChatReview, summarizeChatReview } from "../../api/client";
import type { ChatReviewSummary } from "../../types/api";
import { turnsToMessages, type Turn } from "./useChat";

type Phase = "idle" | "summarizing" | "summarized" | "sending" | "sent" | "error";

type Props = {
  turns: Turn[];
  /** 増えたら「まとめる」を始める合図。押した回数そのものに意味は無い */
  startSignal: number;
};

export function ChatReviewPanel({ turns, startSignal }: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [summary, setSummary] = useState<ChatReviewSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 送信後に会話が続いた場合、古い要約のまま「送信済み」と表示され続けると
  // 何を送ったのか分からなくなるため、件数が増えたら状態を戻す
  const [snapshotCount, setSnapshotCount] = useState<number | null>(null);
  const handledSignal = useRef(startSignal);

  const summarize = useCallback(async () => {
    setPhase("summarizing");
    setError(null);
    try {
      const result = await summarizeChatReview(turnsToMessages(turns));
      setSummary(result);
      setPhase("summarized");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }, [turns]);

  // 押し直しでやり直せるようにする。まとめ直しても副作用は無い
  // （送信は別のボタン）ので、状態を見て弾く必要がない
  useEffect(() => {
    if (startSignal === handledSignal.current) return;
    handledSignal.current = startSignal;
    void summarize();
  }, [startSignal, summarize]);

  useEffect(() => {
    if (snapshotCount !== null && turns.length > snapshotCount) {
      setPhase("idle");
      setSummary(null);
      setError(null);
      setSnapshotCount(null);
    }
  }, [turns.length, snapshotCount]);

  const messages = turnsToMessages(turns);
  if (messages.length === 0) return null;

  async function sendToSupervisor() {
    setPhase("sending");
    setError(null);
    try {
      await sendChatReview(messages);
      setSnapshotCount(turns.length);
      setPhase("sent");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }

  /** まとめただけで送らない選択。要約を捨てて「まとめる」ボタンに戻す */
  function dismiss() {
    setPhase("idle");
    setSummary(null);
    setError(null);
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-2">
      {phase === "summarizing" && <p className="px-2 text-xs text-slate-400">まとめています…</p>}

      {(phase === "summarized" || phase === "sending") && summary && (
        <div className="rounded-xl bg-white p-3 text-sm ring-1 ring-slate-200/80">
          <p className="text-slate-800">{summary.summary}</p>

          {summary.understood_points.length > 0 && (
            <div className="mt-2">
              <p className="text-xs font-medium text-slate-500">理解できていた事項</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-slate-600">
                {summary.understood_points.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          {summary.knowledge_gaps.length > 0 && (
            <div className="mt-2">
              <p className="text-xs font-medium text-amber-700">ナレッジDBに不足していた点</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-amber-800">
                {summary.knowledge_gaps.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-3 flex gap-2">
            <button
              onClick={() => void sendToSupervisor()}
              disabled={phase === "sending"}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white
                         hover:bg-indigo-500 disabled:opacity-50"
            >
              {phase === "sending" ? "送信しています…" : "上司に送信する"}
            </button>
            <button
              onClick={dismiss}
              disabled={phase === "sending"}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-slate-500
                         hover:bg-slate-100 disabled:opacity-50"
            >
              送信しない
            </button>
          </div>
        </div>
      )}

      {phase === "sent" && (
        <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          上司に送信しました。「上司レビュー」タブで回答を確認できます。
        </p>
      )}

      {phase === "error" && error && (
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-800">{error}</p>
      )}
    </div>
  );
}
