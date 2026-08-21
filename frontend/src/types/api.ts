/**
 * バックエンドのレスポンス型。
 *
 * 手書きするとバックエンドとズレるため、実装が固まったら
 * OpenAPIスキーマから自動生成する方針（CLAUDE.md 5章）。
 *   npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/types/api.d.ts
 *
 * 現時点では手で定義している。backend/app/models/knowledge.py と対応させること。
 */

export type KnowledgeStatus = "draft" | "confirmed" | "rejected";
export type SourceType = "manual" | "meeting" | "audio";

export type Knowledge = {
  id: string;
  content: string;
  original_content: string | null;
  status: KnowledgeStatus;
  source_type: SourceType;
  source_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

/** 検索結果。score は類似度だが参考値であり、しきい値判定には使わない。 */
export type KnowledgeSearchResult = Knowledge & {
  score: number;
};

export type KnowledgeCounts = {
  draft: number;
  confirmed: number;
  rejected: number;
  total: number;
};

// --- 疎通確認用 ---

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
