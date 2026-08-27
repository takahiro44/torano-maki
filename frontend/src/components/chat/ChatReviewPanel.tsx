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
 *
 * **まとめている最中を実況する。** 要約は vLLM への1往復で数十秒かかり、
 * そのあと疑問点ごとにナレッジDBを引く。以前はその間ずっと
 * 「まとめています…」の一行しか出ておらず、止まったのか動いているのかを
 * 判断する材料が無かった。出しているのは実際に踏んだ工程だけで、
 * 割合も残り時間も出さない（ExtractionProgress と同じ理由）。
 *
 * **疑問点をDBの状態で分ける。** 「DBに無い（上司にしか無い知見）」と
 * 「DBに有るのに後輩が辿り着けなかった」は、上司がやるべきことが違う。
 * 前者は答えを書く必要があり、後者は既存ナレッジの適用場面を直すだけで済む。
 * 同じ「不足していた点」として並べると、この判別を毎回上司の頭にやらせることになる。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { sendChatReview, streamChatReviewSummary } from "../../api/client";
import type { ChatReviewDiagnosis, GapDiagnosis } from "../../types/api";
import { AgentTimeline, type TimelineStep } from "./AgentTimeline";
import { turnsToMessages, type Turn } from "./useChat";

type Phase = "idle" | "summarizing" | "summarized" | "sending" | "sent" | "error";

type Props = {
  turns: Turn[];
  /** 増えたら「まとめる」を始める合図。押した回数そのものに意味は無い */
  startSignal: number;
};

export function ChatReviewPanel({ turns, startSignal }: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [diagnosis, setDiagnosis] = useState<ChatReviewDiagnosis | null>(null);
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [error, setError] = useState<string | null>(null);
  // 送信後に会話が続いた場合、古い要約のまま「送信済み」と表示され続けると
  // 何を送ったのか分からなくなるため、件数が増えたら状態を戻す
  const [snapshotCount, setSnapshotCount] = useState<number | null>(null);
  const handledSignal = useRef(startSignal);

  const summarize = useCallback(async () => {
    setPhase("summarizing");
    setError(null);
    setSteps([]);
    setDiagnosis(null);

    // done より先に error が来たら、そこで確定させる。
    // ストリームは接続時点で200が返るため、失敗はイベントとしてしか届かない
    let failed: string | null = null;
    let result: ChatReviewDiagnosis | null = null;

    try {
      await streamChatReviewSummary(turnsToMessages(turns), (event) => {
        switch (event.type) {
          case "step":
            setSteps((prev) => [
              ...prev,
              { step: event.step, label: event.label, summary: null, ok: null, errorCode: null },
            ]);
            break;
          case "step_result":
            setSteps((prev) =>
              prev.map((s) =>
                s.step === event.step
                  ? { ...s, summary: event.summary, ok: event.ok, errorCode: event.error_code }
                  : s,
              ),
            );
            break;
          case "done":
            result = event.diagnosis;
            break;
          case "error":
            failed = event.message;
            break;
        }
      });
    } catch (e) {
      failed = e instanceof Error ? e.message : String(e);
    }

    if (failed !== null) {
      setError(failed);
      setPhase("error");
      return;
    }
    if (result === null) {
      // done も error も来ずにストリームが終わった。握り潰すと
      // 「まとめています…」のまま永久に止まって見える
      setError("要約が最後まで届きませんでした。もう一度お試しください。");
      setPhase("error");
      return;
    }
    setDiagnosis(result);
    setPhase("summarized");
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
      setDiagnosis(null);
      setSteps([]);
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
    setDiagnosis(null);
    setSteps([]);
    setError(null);
  }

  const missing = diagnosis?.gaps.filter((g) => g.db_state === "missing") ?? [];
  const reachable = diagnosis?.gaps.filter((g) => g.db_state === "found_but_unreachable") ?? [];

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-2">
      {phase === "summarizing" && (
        <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200/80">
          <AgentTimeline steps={steps} streaming />
        </div>
      )}

      {(phase === "summarized" || phase === "sending") && diagnosis && (
        <div className="rounded-xl bg-white p-3 text-sm ring-1 ring-slate-200/80">
          <p className="text-slate-800">{diagnosis.summary}</p>

          {diagnosis.understood_points.length > 0 && (
            <div className="mt-2">
              <p className="text-xs font-medium text-slate-500">理解できていた事項</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-slate-600">
                {diagnosis.understood_points.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          {/* 上司の時間を使う価値があるのはこちら。並び順はサーバが決めている */}
          {missing.length > 0 && (
            <div className="mt-2.5">
              <p className="text-xs font-medium text-amber-700">上司に答えてほしいこと</p>
              <ul className="mt-1 space-y-1">
                {missing.map((g, i) => (
                  <li key={i} className="rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">
                    {g.gap}
                    <span className="mt-0.5 block text-[10.5px] text-amber-700/70">
                      近いナレッジが蓄積に無い
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {reachable.length > 0 && (
            <div className="mt-2.5">
              <p className="text-xs font-medium text-slate-500">
                蓄積には有ったが、辿り着けなかった
              </p>
              <ul className="mt-1 space-y-1">
                {reachable.map((g, i) => (
                  <li key={i} className="rounded-lg bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700">
                    {g.gap}
                    <ExistingKnowledge gap={g} />
                  </li>
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

/**
 * 当たった既存ナレッジ。
 *
 * **類似度をそのまま出す。** サーバがこの数字で判定しているため、
 * 出しておかないと上司が判定を検証できない（当たっていないのに
 * 「有る」と言われても、確かめる手がかりが無い）。
 */
function ExistingKnowledge({ gap }: { gap: GapDiagnosis }) {
  if (gap.existing_knowledge.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {gap.existing_knowledge.map((k) => (
        <li key={k.knowledge_id} className="flex items-baseline gap-1.5 text-[11px]">
          <span className="truncate text-indigo-700">{k.title}</span>
          {k.semantic_score !== null && (
            <span className="shrink-0 font-mono text-[10px] text-slate-400">
              {k.semantic_score.toFixed(2)}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
