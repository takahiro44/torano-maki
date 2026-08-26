/**
 * MVPの画面。
 *
 * 「テキストを保存 → Embedding化 → 自然言語で検索」を1画面で確認できるようにする。
 * ルーターは入れていない。画面が4つしかなく、URLを分ける必要が出てから
 * 導入すれば足りるため（依存を増やさない）。
 *
 * **画面の高さを固定し、内側だけをスクロールさせる。** チャットは会話が伸びても
 * 入力欄が下に固定されている必要があり、ページ全体が伸びる作りだと成立しない。
 *
 * **チャットだけは常にマウントしたままにする。** 条件レンダーで切り替えると、
 * 他のタブを見て戻ったときに会話が消える。回答に数十秒かかるので、
 * 待っている間に「探す」を見に行くのは自然な操作であり、そこで消えるのは困る。
 */

import { useEffect, useState } from "react";
import { countKnowledge, getDbHealth } from "./api/client";
import { AiChat } from "./components/chat/AiChat";
import { KnowledgeInput } from "./components/KnowledgeInput";
import { KnowledgeList } from "./components/KnowledgeList";
import { KnowledgeSearch } from "./components/KnowledgeSearch";
import type { KnowledgeCounts } from "./types/api";

type Tab = "chat" | "search" | "input" | "list";

const TABS: { key: Tab; label: string }[] = [
  { key: "chat", label: "AIに聞く" },
  { key: "search", label: "探す" },
  { key: "input", label: "登録" },
  { key: "list", label: "一覧" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
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
    <div className="flex h-dvh flex-col">
      <header className="shrink-0 border-b border-slate-200/80 bg-white/80 backdrop-blur-sm">
        <div className="mx-auto max-w-3xl px-4">
          <div className="flex items-baseline justify-between pt-4 pb-3">
            <h1 className="text-lg font-semibold tracking-tight">torano-maki</h1>
            <p className="text-xs text-slate-400">
              {counts ? `ナレッジ ${counts.confirmed} 件` : "　"}
            </p>
          </div>
          <nav className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                aria-current={tab === t.key ? "page" : undefined}
                className={
                  "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors " +
                  (tab === t.key
                    ? "border-indigo-600 text-slate-900"
                    : "border-transparent text-slate-400 hover:text-slate-600")
                }
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      {apiError && (
        <div className="shrink-0 border-b border-rose-200 bg-rose-50 px-4 py-2.5">
          <div className="mx-auto max-w-3xl text-sm text-rose-800">
            {apiError}
            <div className="mt-1 font-mono text-xs text-rose-600">
              cd backend &amp;&amp; uv run uvicorn app.main:app --reload
            </div>
          </div>
        </div>
      )}

      <main className="min-h-0 flex-1">
        {/* チャットは隠すだけでアンマウントしない（会話と実行中の応答を保つ） */}
        <div className={tab === "chat" ? "h-full" : "hidden"}>
          <AiChat knowledgeCount={counts?.confirmed ?? null} />
        </div>

        {tab !== "chat" && (
          <div className="h-full overflow-y-auto">
            <div className="mx-auto max-w-3xl px-4 py-8">
              {tab === "search" && <KnowledgeSearch />}
              {tab === "input" && <KnowledgeInput onCreated={() => setReloadKey((n) => n + 1)} />}
              {tab === "list" && (
                <KnowledgeList reloadKey={reloadKey} onChanged={() => setReloadKey((n) => n + 1)} />
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
