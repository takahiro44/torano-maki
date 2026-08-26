/**
 * バックエンドAPIの呼び出し口。
 *
 * fetch を各コンポーネントに散らかすと、ベースURLの指定漏れや
 * エラー処理の書き方がバラバラになるため、ここに集約する。
 */

import type {
  ChatMessage,
  ChatResponse,
  ChatStreamEvent,
  ConfigHealthResponse,
  DbHealthResponse,
  Knowledge,
  KnowledgeCounts,
  KnowledgeSearchResult,
  KnowledgeSortField,
  KnowledgeStatus,
  KnowledgeEvidenceSpan,
  SortDirection,
} from "../types/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  // コンストラクタのパラメータプロパティは erasableSyntaxOnly では使えないため、
  // フィールドを明示的に宣言する
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    // FastAPI は detail に理由を入れる。そのまま出した方が原因を追いやすい
    let detail = `${init?.method ?? "GET"} ${path} failed`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = JSON.stringify(body.detail);
    } catch {
      // JSONでないエラー応答は無視して既定のメッセージを使う
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const apiGet = <T>(path: string) => request<T>(path);

// --- Knowledge ---

export type IngestTextResponse = {
  raw_text: string;
  saved: Knowledge[];
  notes: string[];
};

export function ingestText(rawText: string) {
  return request<IngestTextResponse>("/ingest/text", {
    method: "POST",
    body: JSON.stringify({ raw_text: rawText }),
    signal: AbortSignal.timeout(720_000),
  });
}

export function listKnowledge(
  params: {
    status?: KnowledgeStatus;
    industry?: string;
    product?: string;
    sales_stage?: string;
    sort?: KnowledgeSortField;
    order?: SortDirection;
    limit?: number;
  } = {},
) {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.industry) q.set("industry", params.industry);
  if (params.product) q.set("product", params.product);
  if (params.sales_stage) q.set("sales_stage", params.sales_stage);
  if (params.sort) q.set("sort", params.sort);
  if (params.order) q.set("order", params.order);
  q.set("limit", String(params.limit ?? 50));
  return request<Knowledge[]>(`/knowledge?${q}`);
}

export function countKnowledge() {
  return request<KnowledgeCounts>("/knowledge/count");
}

export function updateKnowledge(
  id: string,
  changes: {
    title?: string;
    situation?: string;
    problem?: string;
    judgment?: string;
    action?: string;
    reasoning?: string;
    outcome?: string;
    lesson?: string;
    applicable_situations?: string;
    limitations?: string;
    industry?: string;
    product?: string;
    sales_stage?: string;
    status?: KnowledgeStatus;
  },
) {
  return request<Knowledge>(`/knowledge/${id}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export function deleteKnowledge(id: string) {
  return request<void>(`/knowledge/${id}`, { method: "DELETE" });
}

export function getKnowledge(id: string) {
  return request<Knowledge>(`/knowledge/${id}`);
}

export function getKnowledgeEvidence(id: string) {
  return request<KnowledgeEvidenceSpan[]>(`/knowledge/${id}/evidence`);
}

// --- 検索 ---

export function searchKnowledge(query: string, topK = 5) {
  return request<KnowledgeSearchResult[]>("/search", {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK }),
  });
}

// --- AIチャット ---

/**
 * 蓄積ナレッジをもとにAIへ質問する。
 *
 * **会話履歴は毎回すべて送る。** サーバは履歴を保持しない
 * （認証を作らない方針のため、会話の所有者を定義できない）。
 *
 * **応答に1分近くかかる。** AIが裏で検索と根拠取得を行い、
 * vLLMと複数回やりとりするため。既定のfetchはタイムアウトしないが、
 * 無期限に待つと画面が復帰できなくなるので上限を置いている。
 */
export function sendChat(messages: ChatMessage[], topK = 5) {
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ messages, top_k: topK }),
    signal: AbortSignal.timeout(300_000),
  });
}

// --- 疎通確認 ---

export const getDbHealth = () => request<DbHealthResponse>("/health/db");
export const getConfigHealth = () => request<ConfigHealthResponse>("/health/config");

/**
 * AIチャットをSSEで受け取る。
 *
 * **なぜ `sendChat` と別にするか。**
 * 実測でDGXのdecode速度は約20 tok/s、回答561トークンで約28秒かかる。
 * つまり一括応答の待ち時間の大半は「回答を書いている時間」で、
 * プロンプトを削っても縮まない（4,151→1,838トークンにしても10.5s→10.7s）。
 * 一方、最初の1トークンまでは1.2秒で届く。逐次受け取るだけで
 * 体感の待ち時間が32秒から1〜2秒になる。
 *
 * **EventSource を使えない。** 会話履歴をPOSTのボディで送る必要があり、
 * EventSource は GET しか投げられないため、fetch でストリームを読む。
 *
 * `signal` で中止できる。中止は正常系なので例外にせず、静かに返る。
 */
export async function streamChat(
  messages: ChatMessage[],
  onEvent: (event: ChatStreamEvent) => void,
  options: { topK?: number; signal?: AbortSignal } = {},
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ messages, top_k: options.topK ?? 5 }),
    signal: options.signal,
  });

  // ストリームが始まる前の失敗（503 = 未設定 / 502 = DGXに届かない）は
  // ここで捕まる。始まったあとの失敗は error イベントで届く
  if (!res.ok) {
    let detail = `POST /chat/stream failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // JSONでないエラー応答は既定のメッセージのまま
    }
    throw new ApiError(detail, res.status);
  }
  if (!res.body) throw new ApiError("応答が空でした", res.status);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // CRLF で送ってくる実装もあるため正規化してから区切る
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

      // イベントの区切りは空行。最後の断片は次のチャンクに繰り越す
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseSseBlock(block);
        if (event) onEvent(event);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (e) {
    // 中止は利用者の操作であって異常ではない。呼び出し側に投げ返さない
    if (options.signal?.aborted) return;
    throw e;
  } finally {
    reader.cancel().catch(() => {
      // 既に閉じているストリームのキャンセルは失敗しうる。無視してよい
    });
  }
}

/** SSEの1ブロックを解釈する。壊れた行で全体を止めないよう、駄目なら null を返す */
function parseSseBlock(block: string): ChatStreamEvent | null {
  const data = block
    .split("\n")
    // ":" 始まりはコメント（キープアライブ）。"event:" はこの契約では使わない
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  try {
    return JSON.parse(data) as ChatStreamEvent;
  } catch {
    console.warn("解釈できないSSEイベントを無視しました", data);
    return null;
  }
}
