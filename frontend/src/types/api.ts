/**
 * バックエンドのレスポンス型。
 *
 * 手書きするとバックエンドとズレるため、実装が進んだら
 * OpenAPIスキーマから自動生成する方針（CLAUDE.md 5章）。
 *   npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/types/api.d.ts
 *
 * 現時点では疎通確認に必要な分だけ手で定義している。
 */

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
  detail: string | null;
};

export type ConfigHealthResponse = {
  embedding_configured: boolean;
  embedding_model: string | null;
  embedding_dim: number | null;
  ollama_base_url: string;
  ollama_model: string | null;
};
