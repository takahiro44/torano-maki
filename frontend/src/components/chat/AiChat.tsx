/**
 * AIチャット。蓄積ナレッジをもとに質問へ答える。
 *
 * **画面の高さいっぱいを使い、会話だけが内側でスクロールする。** 以前は記事と
 * 同じ縦積みだったため、会話が伸びると入力欄がページの下へ流れていき、
 * 質問のたびにスクロールして戻る必要があった。
 *
 * **AIの作業を畳まずに見せる。** 「本当にDBを見たのか」を利用者が確認できる
 * ことがこのプロダクトの価値であり、隠すと回答を信じる手がかりが無くなる。
 *
 * **調査の様子は右の面に分ける。** 経過を会話の中に全部積むと質問と回答の間が
 * 遠くなり、会話として読めなくなる。横幅の足りない画面では出さない
 * （会話が細くなる方が損失が大きい）。
 */

import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { AgentPet } from "./AgentPet";
import { AgentWorkspace } from "./AgentWorkspace";
import { currentPhase } from "./phase";
import { ChatReviewPanel } from "./ChatReviewPanel";
import { Composer } from "./Composer";
import { AgentTimeline, Spinner } from "./AgentTimeline";
import { Citations } from "./Citations";
import { Markdown } from "./Markdown";
import { NextActions } from "./NextActions";
import { useChat, type Turn } from "./useChat";

const EXAMPLE_QUESTIONS = [
  "在庫が合わなくて顧客に謝ることになった事例は？",
  "受注入力の負担について顧客は何と言っていた？",
  "段階的な導入はどう提案した？",
  "値引きを求められたときはどう対応した？",
];

/** 質問の吹き出しを上端に寄せるときの余白 */
const TOP_PADDING_PX = 12;

/** 調査ビューの開閉。毎回開き直させないため端末に覚えさせる */
const WORKSPACE_KEY = "torano-maki:chat:workspace";

function loadWorkspaceOpen(): boolean {
  try {
    return localStorage.getItem(WORKSPACE_KEY) !== "0";
  } catch {
    // プライベートモードで読めないことがある。既定（開く）で始めればよい
    return true;
  }
}

type Props = {
  knowledgeCount: number | null;
  /** 調査ビューの待機中一覧の再取得トリガー。登録・音声タブでの登録時にApp側でbumpされる */
  reloadKey: number;
};

export function AiChat({ knowledgeCount, reloadKey }: Props) {
  const { turns, busy, send, stop, reset, retry } = useChat();
  const [workspaceOpen, setWorkspaceOpen] = useState(loadWorkspaceOpen);
  // 「上司に確認する」を押した合図。要約の状態そのものは
  // ChatReviewPanel が持っているので、番号を送って起こすだけにする
  const [reviewSignal, setReviewSignal] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const latestUserBubbleRef = useRef<HTMLDivElement | null>(null);
  const spacerRef = useRef<HTMLDivElement | null>(null);
  const lastTurnId = turns.length > 0 ? turns[turns.length - 1].id : null;
  // 起動直後（StrictModeの開発時二重実行を含む）を判定する基準。
  // 副作用の中で書き換えるフラグにすると、Reactが開発時に行う
  // mount→cleanup→mount の再実行でフラグだけ先に立ってしまい、
  // 再読み込み直後なのに「新しいターン」の分岐に入ってしまう
  const initialTurnIdRef = useRef(lastTurnId);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    if (lastTurnId === initialTurnIdRef.current) {
      // 起動直後は、復元した会話の末尾（直近のやり取り）を見せる
      el.scrollTop = el.scrollHeight;
      return;
    }

    const bubble = latestUserBubbleRef.current;
    if (!bubble) return;

    // 短い回答でも質問を上端まで寄せられるよう、先に1画面分の余白を確保する
    if (spacerRef.current) {
      spacerRef.current.style.minHeight = `${el.clientHeight}px`;
    }
    const containerRect = el.getBoundingClientRect();
    const bubbleRect = bubble.getBoundingClientRect();
    el.scrollTop += bubbleRect.top - containerRect.top - TOP_PADDING_PX;
  }, [lastTurnId]);

  useEffect(() => {
    try {
      localStorage.setItem(WORKSPACE_KEY, workspaceOpen ? "1" : "0");
    } catch {
      // 覚えられなくても開閉そのものは動く
    }
  }, [workspaceOpen]);

  const empty = turns.length === 0;
  // 調査ビューは最新のターンだけを映す。過去の分まで重ねると、
  // どれが今の質問のグラフなのか分からなくなる
  const latest = turns.length > 0 ? turns[turns.length - 1] : null;

  return (
    <div className="flex h-full">
      {/* 調査ビューは会話の左。読む列（会話）を画面の同じ位置に置いたまま、
          開閉できる面を外側に足す。境界の三角タブは開閉どちらでも常に表示し、
          同じボタンで出し入れできるようにする */}
      <div className="hidden shrink-0 lg:flex">
        {workspaceOpen && (
          <div className="w-[360px] xl:w-[420px]">
            <AgentWorkspace turn={latest} reloadKey={reloadKey} />
          </div>
        )}
        <button
          onClick={() => setWorkspaceOpen((v) => !v)}
          aria-label={workspaceOpen ? "調査ビューを閉じる" : "調査ビューを開く"}
          aria-expanded={workspaceOpen}
          className="flex w-5 shrink-0 items-center justify-center border-r border-indigo-100
                     bg-indigo-50/40 text-sm text-slate-300 hover:bg-indigo-100/60 hover:text-slate-600"
        >
          {workspaceOpen ? "◀" : "▶"}
        </button>
      </div>

      {/* 画面全体を歩く。パネルを閉じていても、AIが今どこを見ているかは伝わる */}
      <AgentPet phase={currentPhase(latest)} foundCount={latest?.citations.length ?? 0} />

      <div className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} className="flex-1 overflow-y-auto overscroll-contain">
          <div className="mx-auto max-w-3xl px-4 py-6">
            {empty ? (
              <EmptyState
                knowledgeCount={knowledgeCount}
                onPick={(q) => void send(q)}
                disabled={busy}
              />
            ) : (
              <div className="space-y-8">
                {turns.map((turn) => {
                  const isLatest = turn.id === latest?.id;
                  return (
                    <TurnView
                      key={turn.id}
                      turn={turn}
                      onRetry={() => retry(turn.id)}
                      onReview={() => setReviewSignal((n) => n + 1)}
                      busy={busy}
                      latest={isLatest}
                      anchorRef={isLatest ? latestUserBubbleRef : undefined}
                    />
                  );
                })}
              </div>
            )}
            <div ref={spacerRef} aria-hidden="true" />
          </div>
        </div>

        <div className="mx-auto flex w-full max-w-3xl items-center justify-end gap-1 px-4">
          {!empty && (
            <button
              onClick={reset}
              className="rounded-lg px-2 py-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            >
              会話をリセット
            </button>
          )}
        </div>

        <ChatReviewPanel turns={turns} startSignal={reviewSignal} />

        {/* アシスタントが寄ってくる先。入力欄そのものに印を付けると
            Composer 側の都合に引きずられるので、隣に置いておく */}
        <div data-pet-anchor="composer" aria-hidden="true" className="mx-auto w-full max-w-3xl" />

        <Composer busy={busy} onSend={(t) => void send(t)} onStop={stop} />
      </div>

    </div>
  );
}

function EmptyState({
  knowledgeCount,
  onPick,
  disabled,
}: {
  knowledgeCount: number | null;
  onPick: (q: string) => void;
  disabled: boolean;
}) {
  // ナレッジが無ければ何を聞いても「該当なし」しか返らない。
  // 先に登録へ誘導しないと、AIが壊れているように見える
  if (knowledgeCount === 0) {
    return (
      <div className="rounded-2xl bg-white p-8 text-center ring-1 ring-slate-200/80">
        <AgentMark size="lg" />
        <p className="mt-4 text-sm font-medium text-slate-800">
          まだナレッジが登録されていません
        </p>
        <p className="mt-1 text-sm text-slate-500">
          「ナレッジ登録」から商談の内容を追加すると、AIが検索して答えられるようになります。
        </p>
      </div>
    );
  }

  return (
    <div className="pt-8 pb-4 text-center">
      <AgentMark size="lg" />
      <h2 className="mt-4 text-xl font-semibold tracking-tight text-slate-900">
        蓄積されたナレッジに聞く
      </h2>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-500">
        AIが社内の商談ナレッジを検索して答えます。
        <br />
        該当が無いときは、無いと答えます。
      </p>

      <div className="mt-8 grid gap-2 text-left sm:grid-cols-2">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            onClick={() => onPick(q)}
            disabled={disabled}
            className="group rounded-xl bg-white px-3.5 py-3 text-sm text-slate-600 ring-1
                       ring-slate-200/80 transition-colors hover:bg-indigo-50/60
                       hover:text-slate-900 hover:ring-indigo-200 disabled:opacity-50"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

function TurnView({
  turn,
  onRetry,
  onReview,
  busy,
  latest,
  anchorRef,
}: {
  turn: Turn;
  onRetry: () => void;
  onReview: () => void;
  busy: boolean;
  /** 最新のターンだけがアシスタントの行き先になる。過去の回答を指されても困る */
  latest: boolean;
  /** 送信直後にこの質問を画面上端へ寄せるための参照。最新のターンにだけ渡す */
  anchorRef?: RefObject<HTMLDivElement | null>;
}) {
  const streaming = turn.status === "streaming";

  return (
    <div className="space-y-4">
      <div ref={anchorRef} className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-md bg-slate-900 px-4 py-2.5 text-[15px] leading-6 whitespace-pre-wrap text-white">
          {turn.question}
        </p>
      </div>

      <div className="flex gap-3" aria-busy={streaming}>
        <AgentMark streaming={streaming} />

        <div className="min-w-0 flex-1 space-y-4">
          {(turn.steps.length > 0 || streaming) && (
            <AgentTimeline steps={turn.steps} streaming={streaming} />
          )}

          {turn.answer && (
            <div data-pet-anchor={latest ? "answer" : undefined}>
              <Markdown text={turn.answer} />
              {streaming && (
                <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-slate-400 align-text-bottom" />
              )}
            </div>
          )}

          {turn.usage?.hit_max_iterations && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              調べる回数の上限に達したため、途中までの情報で回答しています。
            </p>
          )}

          {turn.status === "aborted" && (
            <p className="text-xs text-slate-400">中止しました。</p>
          )}

          {turn.status === "error" && turn.error && (
            <div className="rounded-xl bg-rose-50 px-3.5 py-3 text-sm text-rose-800 ring-1 ring-rose-200/70">
              <p>{turn.error}</p>
              <button
                onClick={onRetry}
                disabled={busy}
                className="mt-2 rounded-lg bg-white px-2.5 py-1 text-xs font-medium text-rose-700
                           ring-1 ring-rose-200 hover:bg-rose-100 disabled:opacity-50"
              >
                もう一度試す
              </button>
            </div>
          )}

          {/* **最新のターンにだけ出す。** 過去の回答すべてにボタンが並ぶと、
              会話を読み返すときに邪魔になるうえ、どれが今の話なのか
              分からなくなる（NextActions.tsx） */}
          {latest && <NextActions turn={turn} onReview={onReview} />}

          <div data-pet-anchor={latest && turn.citations.length > 0 ? "citations" : undefined}>
            <Citations citations={turn.citations} question={turn.question} />
          </div>

          {turn.status === "done" && <TurnFooter turn={turn} />}
        </div>
      </div>
    </div>
  );
}

function TurnFooter({ turn }: { turn: Turn }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(turn.answer);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // 権限やHTTP環境でコピーできないことがある。押しても何も起きないだけ
    }
  }

  return (
    <div className="flex items-center gap-3 text-[11px] text-slate-400">
      <button
        onClick={() => void copy()}
        className="rounded px-1.5 py-0.5 hover:bg-slate-100 hover:text-slate-600"
      >
        {copied ? "コピーしました" : "回答をコピー"}
      </button>
      {/* 最初の1文字までの時間を出すのは、待たされている実感と
          実際の応答性のズレを埋めるため（総時間だけだと遅く感じる） */}
      {turn.ttftSec !== null && <span>初回応答 {turn.ttftSec.toFixed(1)}秒</span>}
      {turn.elapsedSec !== null && <span>全体 {turn.elapsedSec.toFixed(1)}秒</span>}
    </div>
  );
}

/** AIの発言であることを示す印。ストリーミング中は回っている */
function AgentMark({ size = "sm", streaming = false }: { size?: "sm" | "lg"; streaming?: boolean }) {
  if (size === "lg") {
    return (
      <span className="mx-auto flex size-11 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-500 shadow-sm shadow-indigo-200">
        <SparkIcon className="size-5 text-white" />
      </span>
    );
  }
  return (
    <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500">
      {streaming ? (
        <Spinner className="size-3.5 text-white" />
      ) : (
        <SparkIcon className="size-3.5 text-white" />
      )}
    </span>
  );
}

function SparkIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" className={className} aria-hidden="true">
      <path
        d="M10 2.5 11.6 7.2 16.5 8.8 11.6 10.4 10 15.1 8.4 10.4 3.5 8.8 8.4 7.2z"
        fill="currentColor"
      />
      <path d="M15.5 13.2 16.3 15.4 18.5 16.2 16.3 17 15.5 19.2 14.7 17 12.5 16.2 14.7 15.4z" fill="currentColor" opacity="0.7" />
    </svg>
  );
}
