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
  ChatReviewDetail,
  ChatReviewListItem,
  ChatReviewStatus,
  ChatReviewSummary,
  ChatStreamEvent,
  ConfigHealthResponse,
  DbHealthResponse,
  InputMode,
  Knowledge,
  KnowledgeCounts,
  KnowledgeDraftFields,
  KnowledgeRefineResponse,
  KnowledgeSearchResult,
  KnowledgeSortField,
  KnowledgeStatus,
  KnowledgeEvidenceSpan,
  RefineMessage,
  RoleplayCategory,
  RoleplaySession,
  RoleplaySessionSummary,
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

/**
 * AIと相談してナレッジを直した案をもらう。**保存はされない。**
 *
 * 反映するかどうかは画面の人が決める。DBに触れないので id を送らず、
 * 登録直後の下書きも、一覧から開いた確定済みも、同じ口で相談できる。
 *
 * **編集中の値をそのまま送る。** 保存済みの値を送ると、人が手で直した
 * 内容をAIが知らないまま書き直し、直したはずの箇所が巻き戻る。
 *
 * 27Bモデルに13項目を書き直させるので1分近くかかることがある。
 */
export function refineKnowledge(params: {
  draft: KnowledgeDraftFields;
  instruction: string;
  history?: RefineMessage[];
  sourceText?: string | null;
}) {
  return request<KnowledgeRefineResponse>("/knowledge/refine", {
    method: "POST",
    body: JSON.stringify({
      draft: params.draft,
      instruction: params.instruction,
      history: params.history ?? [],
      source_text: params.sourceText ?? null,
    }),
    signal: AbortSignal.timeout(300_000),
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

export function searchKnowledge(query: string, topK = 5, knowledgeType?: string) {
  return request<KnowledgeSearchResult[]>("/search", {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK, knowledge_type: knowledgeType ?? null }),
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

// --- 上司レビュー ---

/** 「まとめる」。DBには書き込まない */
export function summarizeChatReview(messages: ChatMessage[]) {
  return request<ChatReviewSummary>("/chat-reviews/summarize", {
    method: "POST",
    body: JSON.stringify({ messages }),
    signal: AbortSignal.timeout(90_000),
  });
}

/** 「上司に送信」。要約をサーバ側で再生成し、pendingで保存する */
export function sendChatReview(messages: ChatMessage[]) {
  return request<ChatReviewDetail>("/chat-reviews", {
    method: "POST",
    body: JSON.stringify({ messages }),
    signal: AbortSignal.timeout(90_000),
  });
}

export function listChatReviews(status?: ChatReviewStatus) {
  const q = status ? `?status=${status}` : "";
  return request<ChatReviewListItem[]>(`/chat-reviews${q}`);
}

export function getChatReview(id: string) {
  return request<ChatReviewDetail>(`/chat-reviews/${id}`);
}

/** 上司の回答。そのままconfirmedのナレッジとして登録される */
export function respondChatReview(id: string, responseText: string) {
  return request<ChatReviewDetail>(`/chat-reviews/${id}/respond`, {
    method: "POST",
    body: JSON.stringify({ response_text: responseText }),
    signal: AbortSignal.timeout(120_000),
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

/**
 * 過去の練習を新しい順に取る。
 *
 * **タイムアウトを指定しない（既定のまま）。** ここはLLMを呼ばないため、
 * 数十秒待つ理由がない。開始画面を開いた直後に出す。
 *
 * `reviewedOnly` は振り返りまで終わった練習だけに絞る。**絞り込みを
 * 受け取ってから捨てない。** 捨てると、取った件数のうち何件が残るか
 * 分からず、一覧が理由もなく空になる。
 */
export function listRoleplaySessions(params?: { limit?: number; reviewedOnly?: boolean }) {
  const query = new URLSearchParams({ limit: String(params?.limit ?? 20) });
  if (params?.reviewedOnly) query.set("reviewed_only", "true");
  return request<RoleplaySessionSummary[]>(`/roleplay/sessions?${query}`);
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
