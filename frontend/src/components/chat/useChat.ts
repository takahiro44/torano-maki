/**
 * AIチャットの状態。
 *
 * **進行中のターンと完了したターンを同じ型で持つ。** 以前は `pending`（質問文）と
 * `turns`（完了分）に分かれており、進行中は「経過秒数から推測した文言」しか
 * 出せなかった。ストリーミングでは回答も出典も途中経過として届くため、
 * 最初から同じ入れ物に積んでいく形にする。
 *
 * **会話履歴はこの画面が持つ。** サーバは保持しない（認証を作らない方針のため
 * 会話の所有者を定義できない）。送信のたびに全履歴を組み立てて送る。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { streamChat } from "../../api/client";
import type { ChatMessage, ChatUsage, Citation } from "../../types/api";
import { loadTurns, saveTurns } from "./storage";

/** Agentが実行したTool 1件。`tool_call` で作り、`tool_result` で埋める */
export type AgentStep = {
  step: number;
  tool: string;
  /** 実行前に出す日本語。サーバが決める */
  label: string;
  /** 実行後に届く1行。null は実行中 */
  summary: string | null;
  /** null は実行中 */
  ok: boolean | null;
  errorCode: string | null;
  /** `tool_call` に載っていた引数。何を条件に探したかを調査ビューで見せる */
  args: Record<string, unknown>;
  /**
   * この実行で新しく現れた出典のID。
   *
   * `citations` はどのToolの結果か書かずに全件で届く。ただしTool実行の
   * 直後に流れるため、「前回から増えた分は直前のToolが見つけたもの」と
   * 対応づけられる。この対応が無いと、調査ビューでどの検索が何を
   * 掘り当てたのかを線で結べない。
   */
  foundIds: string[];
};

export type TurnStatus = "streaming" | "done" | "error" | "aborted";

export type Turn = {
  id: string;
  question: string;
  answer: string;
  steps: AgentStep[];
  citations: Citation[];
  usage: ChatUsage | null;
  status: TurnStatus;
  error: string | null;
  /** 最初の1文字が届くまでの秒数。ストリーミングの効きを画面で示すため */
  ttftSec: number | null;
  elapsedSec: number | null;
};

/**
 * 履歴として送るターン数の上限。
 *
 * サーバの `ChatRequest.messages` が最大40件のため、20往復を超えると
 * 422 で弾かれる。長い会話の途中で突然エラーになるより、古い往復を
 * 落として動き続ける方がよい。モデルの `max_model_len` は100万あるので、
 * 長さそのものが問題になっているわけではない。
 */
const MAX_HISTORY_TURNS = 9;

/**
 * 1回の検索でAgentに渡すナレッジの件数。
 *
 * **増やさない。** 12件にして実測したところ、初回応答が6.4秒→13.1秒、
 * 全体が20.1秒→37.1秒になった。1件がCBRの全項目を含むため、
 * 件数がそのままプロンプトの長さになる。
 * 「もっと探している様子を見せたい」は、AgentWorkspace が同じ検索を
 * 別に投げて候補を描くことで満たしている（LLMのプロンプトは増えない）。
 */
const SEARCH_TOP_K = 5;

function usableTurns(turns: Turn[]): Turn[] {
  return turns.filter((t) => t.status === "done" && t.answer.trim()).slice(-MAX_HISTORY_TURNS);
}

function buildMessages(turns: Turn[], question: string): ChatMessage[] {
  const messages = turnsToMessages(turns);
  messages.push({ role: "user", content: question });
  return messages;
}

/**
 * 完了した往復だけをAPIの履歴形式に変換する。
 *
 * 上司レビューの「まとめる」でも同じ履歴が要る。送信中の質問を
 * 末尾に足さない点だけが `buildMessages` と違うため、ここを共有する。
 */
export function turnsToMessages(turns: Turn[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  for (const turn of usableTurns(turns)) {
    messages.push({ role: "user", content: turn.question });
    messages.push({ role: "assistant", content: turn.answer });
  }
  return messages;
}

export function useChat() {
  const [turns, setTurns] = useState<Turn[]>(loadTurns);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // 完了した会話だけ残す。リロードで消えると、時間をかけて得た回答を
  // もう一度1分待って出し直すことになるため
  useEffect(() => {
    if (!busy) saveTurns(turns);
  }, [turns, busy]);

  // 画面を離れるときに走っているリクエストを止める。
  // 放置するとバックエンド側がクライアントの居ないストリームを書き続ける
  useEffect(() => () => abortRef.current?.abort(), []);

  const patchTurn = useCallback((id: string, patch: (turn: Turn) => Turn) => {
    setTurns((prev) => prev.map((turn) => (turn.id === id ? patch(turn) : turn)));
  }, []);

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || abortRef.current) return;

      const id = crypto.randomUUID();
      const startedAt = Date.now();
      const controller = new AbortController();
      abortRef.current = controller;
      setBusy(true);

      const history = buildMessages(turns, question);
      setTurns((prev) => [
        ...prev,
        {
          id,
          question,
          answer: "",
          steps: [],
          citations: [],
          usage: null,
          status: "streaming",
          error: null,
          ttftSec: null,
          elapsedSec: null,
        },
      ]);

      const sinceStart = () => (Date.now() - startedAt) / 1000;

      try {
        await streamChat(
          history,
          (event) => {
            switch (event.type) {
              case "tool_call":
                patchTurn(id, (turn) => ({
                  ...turn,
                  steps: [
                    ...turn.steps,
                    {
                      step: event.step,
                      tool: event.tool,
                      label: event.label,
                      summary: null,
                      ok: null,
                      errorCode: null,
                      args: event.arguments,
                      foundIds: [],
                    },
                  ],
                }));
                break;

              case "tool_result":
                patchTurn(id, (turn) => ({
                  ...turn,
                  steps: turn.steps.map((s) =>
                    s.step === event.step
                      ? { ...s, summary: event.summary, ok: event.ok, errorCode: event.error_code }
                      : s,
                  ),
                }));
                break;

              case "citations":
                // 差分ではなく毎回すべて届く契約なので置き換える。
                // 増えた分は直前のToolの成果として記録する（AgentStep.foundIds）
                patchTurn(id, (turn) => {
                  const known = new Set(turn.citations.map((c) => c.knowledge_id));
                  const fresh = event.citations
                    .map((c) => c.knowledge_id)
                    .filter((knowledgeId) => !known.has(knowledgeId));
                  const last = turn.steps.length - 1;
                  return {
                    ...turn,
                    citations: event.citations,
                    steps:
                      fresh.length === 0 || last < 0
                        ? turn.steps
                        : turn.steps.map((s, i) =>
                            i === last ? { ...s, foundIds: [...s.foundIds, ...fresh] } : s,
                          ),
                  };
                });
                break;

              case "text":
                patchTurn(id, (turn) => ({
                  ...turn,
                  answer: turn.answer + event.delta,
                  ttftSec: turn.ttftSec ?? sinceStart(),
                }));
                break;

              case "answer_reset":
                // 前置きがToolの前置きだったと判明した。回答本文を捨てて
                // 書き直させる。TTFTも測り直す（本当の回答はこれから来る）
                patchTurn(id, (turn) => ({ ...turn, answer: "", ttftSec: null }));
                break;

              case "done":
                patchTurn(id, (turn) => ({
                  ...turn,
                  usage: event.usage,
                  status: "done",
                  elapsedSec: sinceStart(),
                }));
                break;

              case "error":
                patchTurn(id, (turn) => ({
                  ...turn,
                  status: "error",
                  error: describeError(event.code, event.message),
                  elapsedSec: sinceStart(),
                }));
                break;
            }
          },
          { topK: SEARCH_TOP_K, signal: controller.signal },
        );

        // `done` も `error` も来ないままストリームが閉じた場合。
        // 何も表示が変わらないと「固まった」ようにしか見えない
        patchTurn(id, (turn) =>
          turn.status !== "streaming"
            ? turn
            : {
                ...turn,
                status: controller.signal.aborted ? "aborted" : "error",
                error: controller.signal.aborted
                  ? null
                  : "応答の途中で接続が切れました。もう一度お試しください。",
                elapsedSec: sinceStart(),
              },
        );
      } catch (e) {
        // 応答が始まる前に中止すると fetch 自体が AbortError で落ちる。
        // 利用者が押した中止を「エラー」として見せないよう、ここで分ける
        const aborted = controller.signal.aborted;
        patchTurn(id, (turn) => ({
          ...turn,
          status: aborted ? "aborted" : "error",
          error: aborted ? null : describeThrown(e),
          elapsedSec: sinceStart(),
        }));
      } finally {
        abortRef.current = null;
        setBusy(false);
      }
    },
    [turns, patchTurn],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setTurns([]);
    saveTurns([]);
  }, []);

  /** 失敗した質問をもう一度送る。打ち直させないため */
  const retry = useCallback(
    (turnId: string) => {
      const target = turns.find((t) => t.id === turnId);
      if (!target || busy) return;
      setTurns((prev) => prev.filter((t) => t.id !== turnId));
      void send(target.question);
    },
    [turns, busy, send],
  );

  return { turns, busy, send, stop, reset, retry };
}

/** 原因を推測しやすい文言にする。どれも使う人の操作ミスではない */
function describeError(code: string, message: string): string {
  switch (code) {
    case "llm_not_configured":
      return "AIサーバが設定されていません。.env の BASE_URL / MODEL_NAME を確認してください。";
    case "llm_unreachable":
      return "AIサーバ（DGX）に接続できません。起動しているか、ネットワークが届くか確認してください。";
    default:
      return message || "回答の生成中に問題が起きました。";
  }
}

function describeThrown(e: unknown): string {
  if (e instanceof DOMException && e.name === "AbortError") return "";
  const message = e instanceof Error ? e.message : String(e);
  if (message.includes("Failed to fetch") || message.includes("NetworkError")) {
    return "バックエンドに接続できません。uvicorn が起動しているか確認してください。";
  }
  if (message.includes("BASE_URL") || message.includes("未設定")) {
    return "AIサーバが設定されていません。.env の BASE_URL / MODEL_NAME を確認してください。";
  }
  return message;
}
