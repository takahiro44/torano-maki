/**
 * MVPの画面。
 *
 * 「テキストを保存 → Embedding化 → 自然言語で検索」を1画面で確認できるようにする。
 * 依存を増やさず、History API を薄く包んだルーターで表示をURLから導く。
 * ロープレは数分続くため、再読込や戻る・進むから復帰できる必要がある。
 *
 * **画面の高さを固定し、内側だけをスクロールさせる。** チャットは会話が伸びても
 * 入力欄が下に固定されている必要があり、ページ全体が伸びる作りだと成立しない。
 *
 * **チャットだけは常にマウントしたままにする。** 条件レンダーで切り替えると、
 * 他のタブを見て戻ったときに会話が消える。回答に数十秒かかるので、
 * 待っている間に他のタブを見に行くのは自然な操作であり、そこで消えるのは困る。
 *
 * **「探す」「一覧」はタブを持たない。** 左の調査ビュー（AgentWorkspace）に
 * 検索ボックスを常時、待機中はランキングの代わりに登録済み一覧を出している。
 * 「音声」「登録」は個別のルート（/audio, /input）のまま、ナビ上だけ
 * 「ナレッジ登録」という1つのホバー/クリックのメニューにまとめている。
 */

import { useEffect, useState } from "react";
import { countKnowledge, getDbHealth } from "./api/client";
import { AiChat } from "./components/chat/AiChat";
import { AudioIngest } from "./components/AudioIngest";
import { KnowledgeInput } from "./components/KnowledgeInput";
import { NavGroup } from "./components/NavGroup";
import { Roleplay } from "./components/roleplay/Roleplay";
import { SupervisorInbox } from "./components/SupervisorInbox";
import { navigate, readRoleplaySeed, useRoutePath } from "./lib/router";
import type { KnowledgeCounts } from "./types/api";

type Tab = "chat" | "input" | "audio" | "supervisor" | "roleplay";

type NavEntry =
  | { kind: "tab"; key: Tab; label: string }
  | { kind: "group"; label: string; items: { key: Tab; label: string }[] };

const NAV: NavEntry[] = [
  { kind: "tab", key: "chat", label: "AIに聞く" },
  {
    kind: "group",
    label: "ナレッジ登録",
    items: [
      { key: "input", label: "テキストで登録" },
      { key: "audio", label: "音声で登録" },
    ],
  },
  { kind: "tab", key: "supervisor", label: "上司レビュー" },
  { kind: "tab", key: "roleplay", label: "ロープレ" },
];

function tabFromRoute(route: string): Tab {
  const path = route.split("?", 1)[0];
  if (path === "/input") return "input";
  if (path === "/audio") return "audio";
  if (path === "/supervisor") return "supervisor";
  if (path === "/roleplay" || path.startsWith("/roleplay/")) return "roleplay";
  return "chat";
}

function tabPath(tab: Tab): string {
  return `/${tab}`;
}

export default function App() {
  const route = useRoutePath();
  const tab = tabFromRoute(route);
  // 登録・更新のたびに一覧と件数を取り直すためのトリガー
  const [reloadKey, setReloadKey] = useState(0);
  const [counts, setCounts] = useState<KnowledgeCounts | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  const onChanged = () => setReloadKey((n) => n + 1);

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
            {NAV.map((entry) =>
              entry.kind === "tab" ? (
                <button
                  key={entry.key}
                  onClick={() => navigate(tabPath(entry.key))}
                  aria-current={tab === entry.key ? "page" : undefined}
                  className={
                    "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors " +
                    (tab === entry.key
                      ? "border-indigo-600 text-slate-900"
                      : "border-transparent text-slate-400 hover:text-slate-600")
                  }
                >
                  {entry.label}
                </button>
              ) : (
                <NavGroup
                  key={entry.label}
                  label={entry.label}
                  items={entry.items}
                  active={entry.items.some((i) => i.key === tab)}
                  onNavigate={(key) => navigate(tabPath(key))}
                />
              ),
            )}
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
          <AiChat knowledgeCount={counts?.confirmed ?? null} reloadKey={reloadKey} />
        </div>

        {tab !== "chat" && (
          <div className="h-full overflow-y-auto">
            <div className="mx-auto max-w-3xl px-4 py-8">
              {tab === "input" && <KnowledgeInput onCreated={onChanged} />}
              {tab === "audio" && <AudioIngest onChanged={onChanged} />}
              {tab === "supervisor" && <SupervisorInbox />}
              {tab === "roleplay" && (
                <Roleplay {...readRoleplaySeed()} />
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
