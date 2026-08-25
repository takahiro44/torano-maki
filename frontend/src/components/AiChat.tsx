/**
 * AIチャット。蓄積ナレッジをもとに質問へ答える。
 *
 * **応答に1分近くかかる。** AIが裏で検索と根拠取得を行うため。
 * 無言で待たせると「固まった」と思われるので、経過秒数を出し続ける。
 *
 * **会話履歴はこの画面が持つ。** サーバは保持しないため、
 * 送信のたびに全履歴を組み立てて送る（api/client.ts の sendChat 参照）。
 */

import { useEffect, useRef, useState } from "react";
import { sendChat } from "../api/client";
import type { ChatMessage, ChatResponse, Citation, ToolTraceStep } from "../types/api";

/** 1往復ぶん。回答に紐づく出典とtraceを一緒に持たせるため、配列を分けない */
type Turn = {
  question: string;
  response: ChatResponse | null;
  error: string | null;
  elapsedSec: number | null;
};

const EXAMPLE_QUESTIONS = [
  "在庫が合わなくて顧客に謝ることになった事例は？",
  "受注入力の負担について顧客は何と言っていた？",
  "段階的な導入はどう提案した？",
];

const SPEAKER_LABEL: Record<string, string> = {
  salesperson: "営業",
  customer: "顧客",
  source: "原文",
  unknown: "不明",
};

/** 待たせている間に出す文言。秒数だけだと何を待っているか分からない */
function waitingLabel(sec: number): string {
  if (sec < 5) return "考えています…";
  if (sec < 20) return "ナレッジを検索しています…";
  if (sec < 45) return "根拠を確認しています…";
  return "回答を作成しています…";
}

/** 会話履歴をAPIの形に組み立てる。失敗した往復は履歴に含めない */
function toMessages(turns: Turn[], question: string): ChatMessage[] {
  const messages: ChatMessage[] = [];
  for (const turn of turns) {
    if (!turn.response) continue;
    messages.push({ role: "user", content: turn.question });
    messages.push({ role: "assistant", content: turn.response.answer });
  }
  messages.push({ role: "user", content: question });
  return messages;
}

export function AiChat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  // 完了時に「何秒かかったか」を残すため。描画には elapsed を使い、
  // 記録には ref を使う（送信ハンドラ側で時刻を測るとレンダーが不純になる）
  const elapsedRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 経過秒数。応答が長いため、動いていることを示し続ける必要がある
  useEffect(() => {
    if (pending === null) return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      const value = Math.floor((Date.now() - started) / 1000);
      elapsedRef.current = value;
      setElapsed(value);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [pending]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, pending]);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || pending !== null) return;

    setQuestion("");
    elapsedRef.current = 0;
    setElapsed(0);
    setPending(trimmed);
    try {
      const response = await sendChat(toMessages(turns, trimmed));
      setTurns((prev) => [
        ...prev,
        { question: trimmed, response, error: null, elapsedSec: elapsedRef.current },
      ]);
    } catch (e) {
      setTurns((prev) => [
        ...prev,
        { question: trimmed, response: null, error: describeError(e), elapsedSec: null },
      ]);
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">AIに聞く</h2>
        <p className="mt-1 text-sm text-slate-500">
          蓄積されたナレッジをAIが検索して答えます。無い場合は「無い」と答えます。
        </p>
      </div>

      {turns.length === 0 && pending === null && (
        <div className="flex flex-wrap gap-2">
          {EXAMPLE_QUESTIONS.map((q) => (
            <button
              key={q}
              onClick={() => void ask(q)}
              className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs
                         text-slate-600 hover:border-slate-400 hover:text-slate-900"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      <div className="space-y-4">
        {turns.map((turn, i) => (
          <TurnView key={i} turn={turn} />
        ))}

        {pending !== null && (
          <>
            <QuestionBubble text={pending} />
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <p className="flex items-center gap-2 text-sm text-slate-500">
                <span className="inline-block size-2 animate-pulse rounded-full bg-slate-400" />
                {waitingLabel(elapsed)}
                <span className="text-xs text-slate-400">{elapsed}秒</span>
              </p>
              <p className="mt-2 text-xs text-slate-400">
                検索と根拠の確認を挟むため、1分ほどかかることがあります。
              </p>
            </div>
          </>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void ask(question)}
          disabled={pending !== null}
          placeholder="例）値引きを求められたときはどう対応した？"
          className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm
                     outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200
                     disabled:bg-slate-100"
        />
        <button
          onClick={() => void ask(question)}
          disabled={pending !== null || !question.trim()}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                     hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {pending !== null ? "応答中…" : "送信"}
        </button>
      </div>

      {turns.length > 0 && pending === null && (
        <button
          onClick={() => setTurns([])}
          className="text-xs text-slate-400 underline hover:text-slate-600"
        >
          会話をリセット
        </button>
      )}
    </div>
  );
}

function QuestionBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-slate-900 px-4 py-2 text-sm text-white">
        {text}
      </p>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  return (
    <div className="space-y-3">
      <QuestionBubble text={turn.question} />

      {turn.error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          {turn.error}
        </div>
      )}

      {turn.response && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <AnswerText text={turn.response.answer} />

          {turn.response.usage.hit_max_iterations && (
            <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
              調べる回数の上限に達したため、途中までの情報で回答しています。
            </p>
          )}

          <ToolTrace steps={turn.response.tool_trace} elapsedSec={turn.elapsedSec} />
          <Citations citations={turn.response.citations} />
        </div>
      )}
    </div>
  );
}

/**
 * 回答本文。
 *
 * モデルが Markdown で返すため、そのまま出すと `##` や `**` が見えてしまう。
 * 見出し・箇条書き・太字だけを最小限に整える。
 * ライブラリ（react-markdown 等）を入れれば正確に描けるが、
 * 依存の追加はチームの合意が要るため入れていない（CLAUDE.md 3章）。
 */
function AnswerText({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <div className="space-y-1 text-sm leading-relaxed text-slate-800">
      {lines.map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={i} className="h-2" />;

        const heading = /^#{1,6}\s+(.*)$/.exec(trimmed);
        if (heading) {
          return (
            <p key={i} className="pt-2 font-semibold text-slate-900">
              {inline(heading[1])}
            </p>
          );
        }

        const bullet = /^[-*]\s+(.*)$/.exec(trimmed);
        if (bullet) {
          return (
            <p key={i} className="flex gap-2 pl-2">
              <span className="text-slate-400">・</span>
              <span>{inline(bullet[1])}</span>
            </p>
          );
        }

        return <p key={i}>{inline(trimmed)}</p>;
      })}
    </div>
  );
}

/** `**強調**` だけを太字にする。それ以外はそのまま文字として出す */
function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i} className="font-semibold text-slate-900">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

function ToolTrace({ steps, elapsedSec }: { steps: ToolTraceStep[]; elapsedSec: number | null }) {
  if (steps.length === 0) {
    return (
      <p className="mt-3 border-t border-slate-100 pt-2 text-xs text-slate-400">
        ナレッジは検索していません（一般的な応答）
        {elapsedSec !== null && ` · ${elapsedSec}秒`}
      </p>
    );
  }
  return (
    <details className="mt-3 border-t border-slate-100 pt-2">
      <summary className="cursor-pointer text-xs text-slate-500">
        AIが調べたこと（{steps.length}件）
        {elapsedSec !== null && ` · ${elapsedSec}秒`}
      </summary>
      <ol className="mt-2 space-y-1">
        {steps.map((step) => (
          <li key={step.step} className="flex gap-2 text-xs">
            <span className={step.ok ? "text-slate-400" : "text-red-500"}>
              {step.ok ? "✓" : "×"}
            </span>
            <span className={step.ok ? "text-slate-600" : "text-red-700"}>{step.summary}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

/**
 * AIが参照したナレッジ。
 *
 * **「回答の引用元」と書かないこと。** 検索は当たったが回答が
 * 「該当なし」になる場合も入るため、引用元と書くと矛盾して見える。
 */
function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <details className="mt-2 border-t border-slate-100 pt-2">
      <summary className="cursor-pointer text-xs text-slate-500">
        AIが参照した情報（{citations.length}件）
      </summary>
      <div className="mt-2 space-y-3">
        {citations.map((c) => (
          <div key={c.knowledge_id} className="rounded-md bg-slate-50 px-3 py-2">
            <p className="text-sm font-medium text-slate-800">{c.title}</p>
            <p className="mt-0.5 text-[11px] text-slate-500">
              出典: {c.file_name ?? c.source_type ?? "手入力"}
              {c.data_source_id && ` · ${c.data_source_id.slice(0, 8)}`}
            </p>
            {c.utterances.length > 0 && (
              <div className="mt-2 space-y-1 border-l-2 border-slate-200 pl-3">
                {c.utterances.map((u) => (
                  <p key={u.sequence_no} className="text-xs leading-relaxed text-slate-700">
                    <span className="text-slate-400">
                      {SPEAKER_LABEL[u.speaker] ?? u.speaker}
                      {u.end_sec > 0.05 && ` ${u.start_sec.toFixed(0)}秒`}
                    </span>{" "}
                    {u.content}
                  </p>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

/** 原因を推測しやすい文言にする。502 / 503 はAI側の問題で、使う人の操作ミスではない */
function describeError(e: unknown): string {
  const message = e instanceof Error ? e.message : String(e);
  if (e instanceof Error && e.name === "TimeoutError") {
    return "応答が返りませんでした。AIサーバが混んでいる可能性があります。もう一度お試しください。";
  }
  if (message.includes("BASE_URL") || message.includes("未設定")) {
    return "AIサーバが設定されていません。.env の BASE_URL / MODEL_NAME を確認してください。";
  }
  if (message.includes("接続できません")) {
    return "AIサーバ（DGX）に接続できません。起動しているか確認してください。";
  }
  return message;
}
