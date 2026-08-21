/**
 * MVPの画面。
 *
 * 「テキストを保存 → Embedding化 → 自然言語で検索」を1画面で確認できるようにする。
 * ルーターは入れていない。画面が3つしかなく、URLを分ける必要が出てから
 * 導入すれば足りるため（依存を増やさない）。
 */

import { useEffect, useState } from "react";
import { countKnowledge, getDbHealth } from "./api/client";
import { KnowledgeInput } from "./components/KnowledgeInput";
import { KnowledgeList } from "./components/KnowledgeList";
import { KnowledgeSearch } from "./components/KnowledgeSearch";
import type { KnowledgeCounts } from "./types/api";

type Tab = "search" | "input" | "list";

const TABS: { key: Tab; label: string }[] = [
  { key: "search", label: "探す" },
  { key: "input", label: "登録" },
  { key: "list", label: "一覧" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("search");
  // 登録・更新のたびに一覧と件数を取り直すためのトリガー
  const [reloadKey, setReloadKey] = useState(0);
  const [counts, setCounts] = useState<KnowledgeCounts | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  useEffect(() => {
    countKnowledge()
      .then((c) => {
        setCounts(c);
        setApiError(null);
      })
      .catch((e: unknown) => setApiError(e instanceof Error ? e.message : String(e)));
  }, [reloadKey]);

  // バックエンドに到達できない場合、原因が分かるよう案内を出す
  useEffect(() => {
    getDbHealth().catch(() =>
      setApiError("バックエンドに接続できません。uvicorn が起動しているか確認してください"),
    );
  }, []);

  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight">torano-maki</h1>
        <p className="mt-1 text-sm text-slate-500">
          営業ナレッジを貯めて、意味で探す
          {counts && <span className="ml-2 text-slate-400">登録 {counts.confirmed} 件</span>}
        </p>
      </header>

      {apiError && (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {apiError}
          <div className="mt-1 font-mono text-xs text-red-600">
            cd backend &amp;&amp; uv run uvicorn app.main:app --reload
          </div>
        </div>
      )}

      <nav className="mb-6 flex gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={
              "-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors " +
              (tab === t.key
                ? "border-slate-900 text-slate-900"
                : "border-transparent text-slate-400 hover:text-slate-600")
            }
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main>
        {tab === "search" && <KnowledgeSearch />}
        {tab === "input" && <KnowledgeInput onCreated={() => setReloadKey((n) => n + 1)} />}
        {tab === "list" && <KnowledgeList reloadKey={reloadKey} />}
      </main>
    </div>
  );
}
