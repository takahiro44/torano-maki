/**
 * バックエンドAPIの呼び出し口。
 *
 * fetch を各コンポーネントに散らかすと、ベースURLの指定漏れや
 * エラー処理の書き方がバラバラになるため、ここに集約する。
 */

import type {
  ConfigHealthResponse,
  DbHealthResponse,
  Knowledge,
  KnowledgeCounts,
  KnowledgeSearchResult,
  KnowledgeStatus,
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

export function listKnowledge(params: { status?: KnowledgeStatus; limit?: number } = {}) {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  q.set("limit", String(params.limit ?? 50));
  return request<Knowledge[]>(`/knowledge?${q}`);
}

export function countKnowledge() {
  return request<KnowledgeCounts>("/knowledge/count");
}

export function updateKnowledge(
  id: string,
  changes: { title?: string; situation?: string; status?: KnowledgeStatus },
) {
  return request<Knowledge>(`/knowledge/${id}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export function deleteKnowledge(id: string) {
  return request<void>(`/knowledge/${id}`, { method: "DELETE" });
}

// --- 検索 ---

export function searchKnowledge(query: string, topK = 5) {
  return request<KnowledgeSearchResult[]>("/search", {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK }),
  });
}

// --- 疎通確認 ---

export const getDbHealth = () => request<DbHealthResponse>("/health/db");
export const getConfigHealth = () => request<ConfigHealthResponse>("/health/config");
