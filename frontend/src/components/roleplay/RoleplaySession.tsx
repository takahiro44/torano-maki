/**
 * 練習中の画面。状況カード・会話・回答欄。
 *
 * **模範解答を先に見せない。** 参照した社内事例は振り返りまで出さない。
 * 先に見えると、考える前に写して終わってしまう。
 *
 * 顧客役の生成に十数秒かかるため、待っている間も何をしているかを出す。
 *
 * **顧客役をアシスタントに演じさせる。** 相手に顔があるだけで、練習が
 * 「フォームに文章を打つ作業」から「誰かと話すこと」に変わる。十数秒の
 * 沈黙も、考えている顔があれば待てる（AgentPet と同じ理由）。
 *
 * **ここの子は歩かない・しまえない。** 徘徊するアシスタントは「見てほしい
 * ものの隣に立つ」役だが、この子は会話の相手そのものである。歩き回られると
 * 誰に向かって話しているのか分からなくなり、消せてしまうと相手のいない
 * 練習画面になる（AgentPet.tsx の PetFace）。
 *
 * **姿の既定はロボット。** いつもの虎と同じ姿だと「味方が喋っている」ように
 * 見えて、顧客の言葉として受け取れない（lib/pet.ts の DEFAULT_SKIN）。
 */

import { useEffect, useState } from "react";
import { finishRoleplay, sendRoleplayTurn } from "../../api/client";
import type { InputMode, RoleplaySession as Session } from "../../types/api";
import { PetFace, type PetMood } from "../chat/AgentPet";
import { MicAnswer } from "./MicAnswer";

type Props = {
  session: Session;
  onUpdated: (session: Session) => void;
};

export function RoleplaySessionView({ session, onUpdated }: Props) {
  const [draft, setDraft] = useState("");
  // マイクから入れた回答は audio として記録する。テキストで打ち直したら text へ戻す
  const [inputMode, setInputMode] = useState<InputMode>("text");
  const [pending, setPending] = useState<null | "turn" | "feedback">(null);
  // 送信した自分の発言。サーバの応答を待たずに会話へ並べるために持つ
  const [sendingContent, setSendingContent] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const scenario = session.scenario;
  const done = session.remaining_learner_turns === 0;
  const mood = customerMood({ pending, done, failed: error !== null });

  useEffect(() => {
    if (pending === null) return;
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [pending]);

  async function send() {
    const content = draft.trim();
    if (!content || pending !== null) return;
    setError(null);
    setElapsed(0);
    setPending("turn");
    // 顧客役の生成に十数秒かかるため、応答を待ってから自分の発言を出すと
    // 「送信できたのか」が分からない時間が生まれる。先に会話へ並べる
    setSendingContent(content);
    setDraft("");
    try {
      const updated = await sendRoleplayTurn(session.session_id, content, inputMode);
      setInputMode("text");
      onUpdated(updated);
    } catch (e) {
      // 失敗しても入力は消さない。数十秒かけて話した内容を打ち直させない
      setDraft(content);
      setError(describeError(e));
    } finally {
      // 応答が返れば同じ発言が session.turns に入る。二重に出さないよう必ず消す
      setSendingContent(null);
      setPending(null);
    }
  }

  async function finish() {
    if (pending !== null) return;
    setError(null);
    setElapsed(0);
    setPending("feedback");
    try {
      onUpdated(await finishRoleplay(session.session_id));
    } catch (e) {
      setError(describeError(e));
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="space-y-4">
      <SituationCard scenario={scenario} />

      <div className="space-y-3">
        {session.turns.map((turn, i) => (
          <div
            key={turn.sequence_no}
            className={turn.role === "learner" ? "flex justify-end" : "flex justify-start gap-2"}
          >
            {turn.role === "customer" && (
              // 気分が動くのは最新の発言だけ。過去の顔まで一緒に変わると、
              // 会話を読み返したときに今どこの話なのか分からなくなる
              <span className="mt-0.5 shrink-0">
                <PetFace
                  scene="roleplay"
                  mood={i === session.turns.length - 1 && sendingContent === null ? mood : "idle"}
                  sizeClass="size-9"
                />
              </span>
            )}
            <div
              className={
                "max-w-[85%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap " +
                (turn.role === "learner"
                  ? "bg-slate-900 text-white"
                  : "border border-slate-200 bg-white text-slate-800")
              }
            >
              {turn.role === "customer" && (
                <p className="mb-1 text-[11px] font-medium text-slate-400">顧客役</p>
              )}
              {turn.content}
            </div>
          </div>
        ))}

        {sendingContent !== null && (
          <div className="flex justify-end">
            <div className="max-w-[85%] rounded-lg bg-slate-900 px-4 py-2 text-sm whitespace-pre-wrap text-white">
              {sendingContent}
            </div>
          </div>
        )}

        {pending === "turn" && (
          <div className="flex justify-start gap-2">
            <span className="mt-0.5 shrink-0">
              <PetFace scene="roleplay" mood="thinking" sizeClass="size-9" />
            </span>
            <p className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm text-slate-500">
              <span className="inline-block size-2 animate-pulse rounded-full bg-slate-400" />
              顧客が考えています…
              <span className="text-xs text-slate-400">{elapsed}秒</span>
            </p>
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      )}

      {pending === "feedback" ? (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="flex items-center gap-2 text-sm text-slate-600">
            <span className="inline-block size-2 animate-pulse rounded-full bg-slate-400" />
            社内の事例と比べています…
            <span className="text-xs text-slate-400">{elapsed}秒</span>
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {done ? (
            <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">
              発言できる回数を使い切りました。振り返りへ進んでください。
            </p>
          ) : (
            <>
              <textarea
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  if (inputMode === "audio") setInputMode("text");
                }}
                // 変換確定のEnterで送ると、十数秒待つ画面では致命的になる。
                // 改行は Shift+Enter、送信は Ctrl/Cmd+Enter に分ける
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) {
                    void send();
                  }
                }}
                rows={3}
                disabled={pending !== null}
                placeholder="顧客にどう返しますか"
                className="w-full resize-y rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm
                           outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200
                           disabled:bg-slate-100"
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <MicAnswer
                  sessionId={session.session_id}
                  disabled={pending !== null}
                  onTranscribed={(text) => {
                    setDraft(text);
                    setInputMode("audio");
                  }}
                />
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-400">
                    あと{session.remaining_learner_turns}回
                  </span>
                  <button
                    onClick={() => void send()}
                    disabled={pending !== null || !draft.trim()}
                    className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                               hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
                  >
                    送信
                  </button>
                </div>
              </div>
            </>
          )}

          <button
            onClick={() => void finish()}
            disabled={pending !== null || session.learner_turns_used === 0}
            className="text-xs text-slate-500 underline hover:text-slate-800
                       disabled:cursor-not-allowed disabled:text-slate-300 disabled:no-underline"
          >
            振り返る
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * 顧客役の気分。
 *
 * **話し終えた直後を「考え中」にしない。** 応答が返った時点で相手は喋り終えて
 * おり、そこで考えている顔をしていると、まだ続きがあるように見える。
 */
function customerMood({
  pending,
  done,
  failed,
}: {
  pending: null | "turn" | "feedback";
  done: boolean;
  failed: boolean;
}): PetMood {
  if (failed) return "sad";
  if (pending === "turn") return "thinking";
  if (pending === "feedback") return "writing";
  return done ? "done" : "idle";
}

function SituationCard({ scenario }: { scenario: Session["scenario"] }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <h2 className="text-base font-semibold text-slate-900">{scenario.title}</h2>
      <dl className="mt-3 space-y-2 text-sm">
        <div>
          <dt className="text-xs font-medium text-slate-500">これまでの状況</dt>
          <dd className="text-slate-800">{scenario.situation}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-slate-500">相手</dt>
          {/* 誰を演じているのかを、姿と説明文を並べて結びつける。
              ここで一度顔を見せておくと、会話に出てくる同じ顔が顧客だと分かる */}
          <dd className="flex items-center gap-2 text-slate-800">
            <PetFace scene="roleplay" mood="idle" sizeClass="size-8" />
            <span className="min-w-0 flex-1">{scenario.customer_persona}</span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-slate-500">今回の目標</dt>
          <dd className="font-medium text-slate-900">{scenario.learner_goal}</dd>
        </div>
      </dl>
    </section>
  );
}

function describeError(e: unknown): string {
  const message = e instanceof Error ? e.message : String(e);
  if (e instanceof Error && e.name === "TimeoutError") {
    return "応答が返りませんでした。入力は残してあるので、もう一度送信してください。";
  }
  if (message.includes("接続できません")) {
    return "AIサーバ（DGX）に接続できません。入力は残してあります。";
  }
  return message;
}
