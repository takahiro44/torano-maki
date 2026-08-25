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
};
