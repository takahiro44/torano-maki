/**
 * バックエンドAPIの呼び出し口。
 *
 * fetch を各コンポーネントに散らかすと、ベースURLの指定漏れや
 * エラー処理の書き方がバラバラになるため、ここに集約する。
 */

import type {
  AudioTranscribeResponse,
  ChatMessage,
  ChatResponse,
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

/** FastAPI の detail をそのまま例外に載せる。原因を追いやすいため */
async function throwIfNotOk(res: Response, fallback: string): Promise<void> {
  if (res.ok) return;
  let detail = fallback;
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") detail = body.detail;
    else if (Array.isArray(body?.detail)) detail = JSON.stringify(body.detail);
  } catch {
    // JSONでないエラー応答は無視して既定のメッセージを使う
  }
  throw new ApiError(detail, res.status);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  await throwIfNotOk(res, `${init?.method ?? "GET"} ${path} failed`);
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

/**
 * 自由テキストを抽出して draft で保存する。
 *
 * dataSourceId を渡すと、そのデータソース（音声など）に紐づく。
 * 渡さなければ source_type='manual' のデータソースが新しく作られる。
 */
export function ingestText(rawText: string, dataSourceId?: string) {
  return request<IngestTextResponse>("/ingest/text", {
    method: "POST",
    body: JSON.stringify({ raw_text: rawText, data_source_id: dataSourceId ?? null }),
    signal: AbortSignal.timeout(720_000),
  });
}

// --- 音声の取り込み ---

/**
 * 音声ファイルを文字起こしする。**同期で待つ。**
 *
 * DGX上のGPUで実時間の約17倍速（8分50秒の音声で31秒）。
 * ジョブ管理を足すコストに見合わないため待つ設計にしている。
 *
 * **Content-Type を自分で付けないこと。** FormData を渡したときに
 * ブラウザが multipart の boundary 付きで設定するため、
 * 手で書くと boundary が欠けてサーバ側でパースに失敗する。
 */
export async function transcribeAudio(file: File, signal?: AbortSignal) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE_URL}/ingest/audio/transcribe`, {
    method: "POST",
    body: form,
    signal: signal ?? AbortSignal.timeout(1_800_000),
  });
  await throwIfNotOk(res, "POST /ingest/audio/transcribe failed");
  return (await res.json()) as AudioTranscribeResponse;
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
