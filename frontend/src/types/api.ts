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

// --- ロープレ ---
//
// バックエンドの models/roleplay.py と対応させる。形の正は OpenAPI スキーマ
// （/openapi.json）で、こちらはそれを写したもの。
//
// **Knowledge ID を持つのは references だけ。** シナリオにも顧客役の発言にも
// IDは入らない。出典はサーバが session に紐づけた記録からのみ組み立てられる
// （AIに書かせると捏造するため）。

export type RoleplayCategory =
  | "needs_discovery"
  | "price_objection"
  | "objection"
  | "complaint"
  | "next_commitment";

export type SessionStatus = "active" | "completed" | "abandoned";
export type TurnRole = "learner" | "customer";
/** generated はAIが作った発言。人の回答（text / audio）と必ず区別する */
export type InputMode = "text" | "audio" | "generated";
export type UsageType = "primary" | "supporting";
export type RubricVerdict = "met" | "partial" | "not_met";

export type CategoryOption = {
  key: RoleplayCategory;
  label: string;
};

/** フィードバックの評価観点。根拠のない総合点を出さないために先に固定する */
export type RubricItem = {
  key: string;
  label: string;
};

export type RoleplayScenario = {
  title: string;
  situation: string;
  learner_goal: string;
  customer_persona: string;
  /** 顧客の最初の発言。この一言から練習が始まる */
  opening_line: string;
  /** 後輩が発言できる最大回数 */
  max_turns: number;
  rubric: RubricItem[];
};

export type RoleplayTurn = {
  sequence_no: number;
  role: TurnRole;
  content: string;
  input_mode: InputMode;
  created_at: string;
};

export type ReferencedUtterance = {
  sequence_no: number;
  speaker: string;
  start_sec: number;
  end_sec: number;
  content: string;
};

/**
 * セッションが実際に使ったナレッジ。
 *
 * `limitations`（使えない場面）を必ず表示すること。成功例の模倣だけを
 * 正解にすると、後輩が場面を選ばず真似てしまう。
 */
export type ReferencedKnowledge = {
  knowledge_id: string;
  title: string;
  usage_type: UsageType;
  rank: number;
  data_source_id: string | null;
  file_name: string | null;
  applicable_situations: string | null;
  limitations: string | null;
  utterances: ReferencedUtterance[];
};

export type RubricResult = {
  key: string;
  verdict: RubricVerdict;
  comment: string;
  label: string;
};

export type RoleplayFeedback = {
  rubric_results: RubricResult[];
  strengths: string[];
  improvements: string[];
  /** 次に試す一言。そのまま口に出せる形で返る */
  next_phrase: string;
  /** 再挑戦時に意識する1点 */
  focus_next_try: string;
  created_at: string;
};

export type RoleplaySession = {
  session_id: string;
  status: SessionStatus;
  query: string;
  scenario: RoleplayScenario;
  turns: RoleplayTurn[];
  references: ReferencedKnowledge[];
  feedback: RoleplayFeedback | null;
  learner_turns_used: number;
  remaining_learner_turns: number;
  created_at: string;
  completed_at: string | null;
};

/**
 * マイク回答の文字起こし。
 *
 * **この時点ではまだ発言として保存されていない。** 誤認識を人が直してから
 * `sendRoleplayTurn` へ送る。誤ったまま進むと顧客役がそれに答え、
 * フィードバックまでその前提で作られる。
 */
export type RoleplayTranscription = {
  text: string;
  language: string | null;
  duration_sec: number;
};
