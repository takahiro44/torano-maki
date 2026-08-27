/**
 * AIチャットの会話を上司に確認してもらうためのパネル。
 *
 * **まとめたら、そのまま送らずに聞き取る。** 会話ログから読み取れるのは
 * 「何が出てこなかったか」までで、本人がどこを怪しいと思っているかは
 * ログに現れない。聞かずに送ると、上司には「AIが読んだ会話」しか届かない
 * （ReviewHearing.tsx）。要約だけ見て送らない選択も残す。
 *
 * **要約はサーバで作り直さない。** 以前は「Agentの自己申告を信用しない」
 * （chat.py）に倣って送信時に再生成していた。聞き取りを挟む今それをやると、
 * 本人が読んで答えたのとは別の文面が上司に届く。信用しないのは
 * **ナレッジDBに有るか無いか**の方で、そこは今もサーバが検索して埋めている。
 *
 * **開始のボタンは持たない。** 会話から他機能へ移る導線は NextActions に
 * 集めてある。ここに独自のボタンを残すと、同じ操作の入口が2つになる。
 *
 * **まとめている最中を実況する。** 要約は vLLM への1往復で数十秒かかり、
 * そのあと疑問点ごとにナレッジDBを引く。出しているのは実際に踏んだ工程だけで、
 * 割合も残り時間も出さない（ExtractionProgress と同じ理由）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { sendChatReview, streamChatReviewSummary } from "../../api/client";
import type { ChatReviewDiagnosis, ReviewHearing as Hearing } from "../../types/api";
import { AgentTimeline, type TimelineStep } from "./AgentTimeline";
import { ReviewHearing } from "./ReviewHearing";
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
  /**
   * この面を開いたときの会話の長さ。
   *
   * **次の質問をしたら閉じる。** 押していないのに聞き取りが出ているように
   * 見えるのは、前の質問で開いた面がそのまま残っているためだった。
   * 会話が1つ進めば、それは別の話であり、そこに前の要約が居座ると
   * 「毎回、上司に送れと言われている」画面になる。
   */
  const openedAt = useRef<number | null>(null);
  const handledSignal = useRef(startSignal);

  const summarize = useCallback(async () => {
    const startedAt = turns.length;
    openedAt.current = startedAt;
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

    // まとめている間に次の質問をされていたら、結果は前の話のもの。捨てる
    if (openedAt.current !== startedAt) return;

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
    if (openedAt.current === null || turns.length === openedAt.current) return;
    openedAt.current = null;
    setPhase("idle");
    setDiagnosis(null);
    setSteps([]);
    setError(null);
  }, [turns.length]);

  const messages = turnsToMessages(turns);
  if (messages.length === 0) return null;

  async function sendToSupervisor(hearing: Hearing) {
    setPhase("sending");
    setError(null);
    try {
      await sendChatReview(messages, hearing);
      setPhase("sent");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // 聞き取った答えを捨てない。ここで idle に戻すと、
      // 一問ずつ答えたものをもう一度全部やり直させることになる
      setPhase("summarized");
    }
  }

  /** まとめただけで送らない選択。要約を捨てて閉じる */
  function dismiss() {
    openedAt.current = null;
    setPhase("idle");
    setDiagnosis(null);
    setSteps([]);
    setError(null);
  }

  return (
    <div className="mx-auto max-h-[46vh] w-full max-w-3xl overflow-y-auto px-4 pb-2">
      {phase === "summarizing" && (
        <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200/80">
          <AgentTimeline steps={steps} streaming />
        </div>
      )}

      {(phase === "summarized" || phase === "sending") && diagnosis && (
        <>
          {/* 要約は読み返すためのもの。**理解できていた事項と疑問点をここに
              並べ直さない。** 同じものを聞き取り（下）がもう一度出すことになり、
              1画面に2回同じ文が並ぶ */}
          <p className="px-1 pb-2 text-xs leading-relaxed text-slate-500">{diagnosis.summary}</p>
          <ReviewHearing
            diagnosis={diagnosis}
            sending={phase === "sending"}
            onSend={(hearing) => void sendToSupervisor(hearing)}
            onCancel={dismiss}
          />
        </>
      )}

      {phase === "sent" && (
        <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          上司に質問を送りました。「上司レビュー」タブで回答を確認できます。
        </p>
      )}

      {/* 失敗しても聞き取りは消さない（sendToSupervisor）。
          エラーだけを添えて、そのまま押し直せるようにする */}
      {error !== null && phase !== "sent" && (
        <p className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-800">{error}</p>
      )}
    </div>
  );
}
