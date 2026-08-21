/**
 * 環境の疎通確認画面。
 *
 * 4人がそれぞれ自分の環境を自力で検証できるようにするために置いている。
 * 「動かない」となったとき、フロント・バックエンド・DBのどこで
 * 止まっているかをこの画面だけで切り分けられる。
 *
 * 機能実装が始まったらこの画面は置き換えてよい。
 */

import { useEffect, useState } from "react";
import { apiGet } from "./api/client";
import type { ConfigHealthResponse, DbHealthResponse, HealthResponse } from "./types/api";

type Load<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "error"; message: string };

function useEndpoint<T>(path: string): Load<T> {
  const [result, setResult] = useState<Load<T>>({ state: "loading" });
  useEffect(() => {
    apiGet<T>(path)
      .then((data) => setResult({ state: "ok", data }))
      .catch((e: unknown) => setResult({ state: "error", message: String(e) }));
  }, [path]);
  return result;
}

function Row({ label, ok, detail }: { label: string; ok: boolean | null; detail: string }) {
  const mark = ok === null ? "…" : ok ? "OK" : "NG";
  const color = ok === null ? "#888" : ok ? "#137333" : "#c5221f";
  return (
    <tr>
      <td style={{ padding: "8px 12px", borderBottom: "1px solid #eee" }}>{label}</td>
      <td style={{ padding: "8px 12px", borderBottom: "1px solid #eee", color, fontWeight: 700 }}>
        {mark}
      </td>
      <td
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid #eee",
          fontFamily: "monospace",
          fontSize: 13,
          wordBreak: "break-all",
        }}
      >
        {detail}
      </td>
    </tr>
  );
}

export default function App() {
  const health = useEndpoint<HealthResponse>("/health");
  const db = useEndpoint<DbHealthResponse>("/health/db");
  const config = useEndpoint<ConfigHealthResponse>("/health/config");

  const dbOk = db.state === "loading" ? null : db.state === "ok" && db.data.status === "ok";
  const vector =
    db.state === "ok" ? db.data.extensions.find((e) => e.name === "vector") : undefined;

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 860, margin: "40px auto", padding: "0 16px" }}>
      <h1 style={{ marginBottom: 4 }}>torano-maki</h1>
      <p style={{ color: "#666", marginTop: 0 }}>環境疎通確認</p>

      <table style={{ borderCollapse: "collapse", width: "100%", marginTop: 24 }}>
        <thead>
          <tr style={{ textAlign: "left", background: "#fafafa" }}>
            <th style={{ padding: "8px 12px" }}>項目</th>
            <th style={{ padding: "8px 12px" }}>状態</th>
            <th style={{ padding: "8px 12px" }}>詳細</th>
          </tr>
        </thead>
        <tbody>
          <Row
            label="フロントエンド"
            ok={true}
            detail="この画面が見えていればOK"
          />
          <Row
            label="バックエンド API"
            ok={health.state === "loading" ? null : health.state === "ok"}
            detail={
              health.state === "error"
                ? "uvicorn が起動しているか確認: cd backend && uv run uvicorn app.main:app --reload"
                : "GET /health"
            }
          />
          <Row
            label="データベース"
            ok={dbOk}
            detail={
              db.state === "ok"
                ? db.data.status === "ok"
                  ? `PostgreSQL ${db.data.postgres_version}`
                  : (db.data.detail ?? "接続失敗")
                : db.state === "error"
                  ? "バックエンドに到達できていない"
                  : "確認中"
            }
          />
          <Row
            label="pgvector"
            ok={db.state === "loading" ? null : Boolean(vector)}
            detail={
              vector
                ? `vector ${vector.version}`
                : "docker compose up -d でDBが起動しているか確認"
            }
          />
          <Row
            label="埋め込み設定"
            ok={config.state === "ok" ? config.data.embedding_configured : null}
            detail={
              config.state === "ok"
                ? config.data.embedding_configured
                  ? `${config.data.embedding_model} / ${config.data.embedding_dim}次元`
                  : "未設定。docs/decisions.md で次元数を決めてから .env に記入する"
                : "確認中"
            }
          />
        </tbody>
      </table>

      <p style={{ color: "#666", fontSize: 13, marginTop: 24 }}>
        「埋め込み設定」が NG なのは想定どおり。モデルと次元数が未確定のため。
      </p>
    </main>
  );
}
