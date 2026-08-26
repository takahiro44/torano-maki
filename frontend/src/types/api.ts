/**
 * バックエンドのレスポンス型。knowledge_units の列と対応させる。
 */

export type KnowledgeStatus = "draft" | "confirmed" | "rejected" | "archived";
export type KnowledgeSortField = "created_at" | "updated_at" | "title" | "status";
export type SortDirection = "asc" | "desc";
export type SourceType = "audio" | "document" | "manual" | "roleplay" | "interview";

export const CBR_FIELD_LABELS: { key: keyof Knowledge; label: string }[] = [
  { key: "title", label: "タイトル" },
  { key: "situation", label: "状況" },
  { key: "problem", label: "顧客課題" },
  { key: "judgment", label: "判断" },
  { key: "action", label: "行動" },
  { key: "reasoning", label: "理由" },
  { key: "outcome", label: "結果" },
  { key: "lesson", label: "学び" },
  { key: "applicable_situations", label: "適用場面" },
  { key: "limitations", label: "制約・非適用" },
  { key: "industry", label: "業界" },
  { key: "product", label: "商材" },
  { key: "sales_stage", label: "商談フェーズ" },
];

/** カード見出し以外の詳細項目 */
export const DETAIL_FIELD_LABELS = CBR_FIELD_LABELS.filter((f) => f.key !== "title");

export type Knowledge = {
  id: string;
  data_source_id: string | null;
  knowledge_type: string;
  title: string;
  situation: string | null;
  problem: string | null;
  judgment: string | null;
  action: string | null;
  reasoning: string | null;
  outcome: string | null;
  lesson: string | null;
  applicable_situations: string | null;
  limitations: string | null;
  industry: string | null;
  product: string | null;
  sales_stage: string | null;
  embedding_model: string | null;
  status: KnowledgeStatus;
  source_id: string | null;
  source_type: SourceType;
  content: string;
  created_at: string;
  updated_at: string;
};

export type KnowledgeSearchResult = Knowledge & {
  score: number;
};

export type Utterance = {
  id: string;
  sequence_no: number;
  speaker: string;
  start_sec: number;
  end_sec: number;
  content: string;
};

export type KnowledgeEvidenceSpan = {
  start_sequence_no: number;
  end_sequence_no: number;
  utterances: Utterance[];
};

export type KnowledgeCounts = {
  draft: number;
  confirmed: number;
  rejected: number;
  archived: number;
  total: number;
};

export type HealthResponse = {
  status: "ok";
};

export type ExtensionInfo = {
  name: string;
  version: string;
};

export type DbHealthResponse = {
  status: "ok" | "error";
  postgres_version: string | null;
  extensions: ExtensionInfo[];
  tables: string[];
  embedding_dim_in_db: number | null;
  embedding_dim_matches: boolean | null;
  detail: string | null;
};

export type ConfigHealthResponse = {
  embedding_configured: boolean;
  embedding_model: string | null;
  embedding_dim: number | null;
  llm_configured: boolean;
  base_url: string | null;
  model_name: string | null;
  stt_configured: boolean;
  stt_base_url: string | null;
  stt_model: string | null;
};

// --- 音声の取り込み ---

export type TranscriptSegment = {
  sequence_no: number;
  start_sec: number;
  end_sec: number;
  text: string;
};

/**
 * 文字起こしの結果。**この時点ではナレッジ化されていない。**
 *
 * 文字起こしの誤り（特に欠落と幻覚）は後段のLLMでは直せないため、
 * 人が確認・修正してから抽出に回す。data_source_id は既に採番されており、
 * ナレッジ化のときに渡すと出典として紐づく。
 */
export type AudioTranscribeResponse = {
  data_source_id: string;
  file_name: string;
  text: string;
  language: string | null;
  duration_sec: number;
  segments: TranscriptSegment[];
};

// --- AIチャット ---
//
// バックエンドの models/chat.py と対応させる。形の正は OpenAPI スキーマ
// （/openapi.json）で、こちらはそれを写したもの。

/** クライアントが送れる役割。system / tool はサーバ側が拒否する */
export type ChatRole = "user" | "assistant";

export type ChatMessage = {
  role: ChatRole;
  content: string;
};

export type CitationUtterance = {
  sequence_no: number;
  speaker: string;
  start_sec: number;
  end_sec: number;
  content: string;
};

/**
 * AIが参照したナレッジ。
 *
 * **「回答の引用元」ではない。** 検索は当たったが回答が「該当なし」に
 * なる場合も入る。どれを引用したかはモデルにしか分からないため、
 * 画面では「AIが参照した情報」として見せること。
 */
export type Citation = {
  knowledge_id: string;
  title: string;
  data_source_id: string | null;
  source_type: string | null;
  file_name: string | null;
  utterances: CitationUtterance[];
};

/** AIが実行したTool 1回分。回答が返るまで長いため、何をしたかを見せる材料 */
export type ToolTraceStep = {
  step: number;
  tool: string;
  ok: boolean;
  summary: string;
  error_code: string | null;
};

export type ChatUsage = {
  iterations: number;
  prompt_tokens: number;
  completion_tokens: number;
  /** 上限に達して打ち切った。true なら回答が不完全な可能性がある */
  hit_max_iterations: boolean;
};

export type ChatResponse = {
  answer: string;
  citations: Citation[];
  tool_trace: ToolTraceStep[];
  usage: ChatUsage;
};

// --- AIチャット（ストリーミング） ---
//
// SSEで届くイベント。契約は docs/chat-stream-contract.md、
// 実装の正はバックエンドの models/chat.py（OpenAPIスキーマ）。
//
// **イベント列にしているのは、Agentが何をしたかを見せること自体が価値だから。**
// 「検索した → 3件見つけた → 答えた」が見えないと、利用者は回答を信じる
// 手がかりを持てない。加えて、最終回答を1トークンずつ流すことで
// 待ち時間の体感が32秒から1〜2秒になる（実測）。

/** Toolを呼ぶと決めた時点。実行前に届く */
export type ChatToolCallEvent = {
  type: "tool_call";
  step: number;
  tool: string;
  /** 画面に出す日本語。サーバが決めるので、フロントにtool名の対応表を持たせない */
  label: string;
  arguments: Record<string, unknown>;
};

/** Toolの実行結果 */
export type ChatToolResultEvent = {
  type: "tool_result";
  step: number;
  tool: string;
  ok: boolean;
  summary: string;
  error_code: string | null;
};

/** 出典。差分ではなく毎回すべて届くので、置き換えるだけでよい */
export type ChatCitationsEvent = {
  type: "citations";
  citations: Citation[];
};

/** 最終回答のトークン */
export type ChatTextEvent = {
  type: "text";
  delta: string;
};

/**
 * ここまで受け取った `text` を破棄する指示。
 *
 * **最終回答のラウンドかどうかは投げてみるまで分からない。** AIは
 * 「根拠の発言を確認します」と前置きしてからToolを呼ぶことがあり、
 * その時点で前置きは既に流れている。破棄できないと前置きが回答として
 * 確定し、本当の回答が返らない。
 */
export type ChatAnswerResetEvent = {
  type: "answer_reset";
  reason: "tool_call";
};

export type ChatDoneEvent = {
  type: "done";
  usage: ChatUsage;
};

/**
 * 異常終了。
 *
 * **HTTPステータスでは表現できない。** SSEは接続確立の時点で200が返るため、
 * そのあとに起きた失敗はイベントとして流すしかない。
 */
export type ChatErrorEvent = {
  type: "error";
  code: "llm_not_configured" | "llm_unreachable" | "internal" | (string & {});
  message: string;
};

export type ChatStreamEvent =
  | ChatToolCallEvent
  | ChatToolResultEvent
  | ChatCitationsEvent
  | ChatTextEvent
  | ChatAnswerResetEvent
  | ChatDoneEvent
  | ChatErrorEvent;
