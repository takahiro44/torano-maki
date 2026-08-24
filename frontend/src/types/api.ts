/**
 * バックエンドのレスポンス型。CBR 列と対応させる。
 */

export type KnowledgeStatus = "draft" | "confirmed" | "rejected" | "archived";
export type SourceType = "audio" | "document" | "manual" | "roleplay" | "interview";

export const CBR_FIELD_LABELS: { key: keyof Knowledge; label: string }[] = [
  { key: "title", label: "タイトル" },
  { key: "situation", label: "状況" },
  { key: "customer_issue", label: "顧客課題" },
  { key: "sales_action", label: "営業対応" },
  { key: "action_reason", label: "対応理由" },
  { key: "result", label: "結果" },
  { key: "learning", label: "学び" },
];

export type Knowledge = {
  id: string;
  data_source_id: string | null;
  knowledge_type: string;
  title: string;
  situation: string | null;
  customer_issue: string | null;
  sales_action: string | null;
  action_reason: string | null;
  result: string | null;
  learning: string | null;
  original_content: string | null;
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
