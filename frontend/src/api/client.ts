/**
 * バックエンドAPIの呼び出し口。
 *
 * fetch を各コンポーネントに散らかすと、ベースURLの指定漏れや
 * エラー処理の書き方がバラバラになるため、ここに集約する。
 */

import type {
  AudioTranscribeResponse,
  CategoryOption,
  ChatMessage,
  ChatResponse,
  ConfigHealthResponse,
  DbHealthResponse,
  InputMode,
  Knowledge,
  KnowledgeCounts,
  KnowledgeSearchResult,
  KnowledgeSortField,
  KnowledgeStatus,
  KnowledgeEvidenceSpan,
  RoleplayCategory,
  RoleplaySession,
  RoleplayTranscription,
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

// --- ロープレ ---
//
// **待ち時間が用途ごとに大きく違う。** セッション作成は検索とシナリオ生成、
// ターンは顧客役の生成、振り返りは講評の生成でそれぞれvLLMを待つ。
// 一律のタイムアウトにすると、短い処理で無駄に待つか、長い処理を
// 正常なのに打ち切ることになるため個別に置いている。

/** 練習できる場面の一覧。ボタンはこの応答から作る（対応表をフロントに持たない） */
export function listRoleplayCategories() {
  return request<CategoryOption[]>("/roleplay/categories");
}

/**
 * 練習を始める。
 *
 * `knowledgeId` を渡すと、そのナレッジを主役にした場面が作られる。
 * AIチャットの「この場面を練習する」から入る経路で使う。
 * 渡さない場合は `query` / `category` からナレッジを検索する。
 *
 * **30秒以上かかる。** 検索・根拠取得・シナリオ生成を順に行うため。
 */
export function startRoleplaySession(params: {
  query?: string;
  knowledgeId?: string;
  category?: RoleplayCategory;
  maxTurns?: number;
}) {
  return request<RoleplaySession>("/roleplay/sessions", {
    method: "POST",
    body: JSON.stringify({
      query: params.query ?? null,
      knowledge_id: params.knowledgeId ?? null,
      category: params.category ?? null,
      max_turns: params.maxTurns ?? 2,
    }),
    signal: AbortSignal.timeout(300_000),
  });
}

/** 再読込・復帰用。シナリオ・発言・出典・振り返りがすべて入って返る */
export function getRoleplaySession(sessionId: string) {
  return request<RoleplaySession>(`/roleplay/sessions/${sessionId}`);
}

/** 回答を送り、顧客役の返答まで進める */
export function sendRoleplayTurn(sessionId: string, content: string, inputMode: InputMode) {
  return request<RoleplaySession>(`/roleplay/sessions/${sessionId}/turns/text`, {
    method: "POST",
    body: JSON.stringify({ content, input_mode: inputMode }),
    signal: AbortSignal.timeout(180_000),
  });
}

/** 振り返りを作って終了する。発言回数が残っていても呼べる */
export function finishRoleplay(sessionId: string) {
  return request<RoleplaySession>(`/roleplay/sessions/${sessionId}/feedback`, {
    method: "POST",
    signal: AbortSignal.timeout(300_000),
  });
}

/** 同じ場面をもう一度。シナリオは作り直さないため待ち時間がない */
export function retryRoleplay(sessionId: string) {
  return request<RoleplaySession>(`/roleplay/sessions/${sessionId}/retry`, { method: "POST" });
}

/**
 * マイク回答を文字起こしする。**まだ発言としては保存されない。**
 *
 * 誤認識を画面で直してから `sendRoleplayTurn` へ送る2段構え。
 *
 * **Content-Type を自分で付けないこと。** FormData を渡したときに
 * ブラウザが multipart の boundary 付きで設定するため、
 * 手で書くと boundary が欠けてサーバ側でパースに失敗する。
 */
export async function transcribeRoleplayAnswer(
  sessionId: string,
  audio: Blob,
  signal?: AbortSignal,
) {
  const form = new FormData();
  // MediaRecorder の Blob にはファイル名が無い。サーバは Content-Type から
  // 拡張子を判定できるが、名前を付けておくと原因追跡が楽になる
  form.append("file", audio, "answer.webm");
  const res = await fetch(`${API_BASE_URL}/roleplay/sessions/${sessionId}/turns/audio`, {
    method: "POST",
    body: form,
    signal: signal ?? AbortSignal.timeout(180_000),
  });
  await throwIfNotOk(res, "POST /roleplay/sessions/{id}/turns/audio failed");
  return (await res.json()) as RoleplayTranscription;
}

// --- 疎通確認 ---

export const getDbHealth = () => request<DbHealthResponse>("/health/db");
export const getConfigHealth = () => request<ConfigHealthResponse>("/health/config");
