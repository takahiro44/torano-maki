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
 *
 * **ナレッジ登録もタブを1つしか持たない。** 以前は「テキストで登録」と
 * 「音声で登録」を分けていたが、抽出の入力は結局テキストで、経路を分ける
 * 理由が無かった（KnowledgeInput の理由）。何で持っているか（メモ・録音・
 * 議事録ファイル）で人に選ばせない。古い `/audio` も同じ画面へ寄せる。
 */

import { useEffect, useState } from "react";
import { countKnowledge, getDbHealth } from "./api/client";
import { AiChat } from "./components/chat/AiChat";
import { KnowledgeInput } from "./components/KnowledgeInput";
import { Roleplay } from "./components/roleplay/Roleplay";
import { SupervisorInbox } from "./components/SupervisorInbox";
import { useHandoff } from "./lib/handoff";
import { setPetHidden, usePetHidden } from "./lib/pet";
import { navigate, readRoleplaySeed, useRoutePath } from "./lib/router";
import type { KnowledgeCounts } from "./types/api";

type Tab = "chat" | "input" | "supervisor" | "roleplay";

const NAV: { key: Tab; label: string }[] = [
  { key: "chat", label: "AIに聞く" },
  { key: "input", label: "ナレッジ登録" },
  { key: "supervisor", label: "上司レビュー" },
  { key: "roleplay", label: "ロープレ" },
];

function tabFromRoute(route: string): Tab {
  const path = route.split("?", 1)[0];
  // `/audio` も登録へ寄せる。画面を1つにした以上、古いURLで迷子にしない
  if (path === "/input" || path === "/audio") return "input";
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
  const petHidden = usePetHidden();
  // 会話から「この話題を登録する」で来たときのきっかけ（質問文）
  const inputHandoff = useHandoff("/input");

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
            <h1 className="text-lg font-semibold tracking-tight">AI虎の巻</h1>
            <div className="flex items-baseline gap-3">
              <p className="text-xs text-slate-400">
                {counts ? `ナレッジ ${counts.confirmed} 件` : "　"}
              </p>
              {/* しまったアシスタントを呼び戻す口。消したきり戻せないと、
                  押すのが怖いボタンになる */}
              {petHidden && (
                <button
                  onClick={() => setPetHidden(false)}
                  className="rounded-lg px-2 py-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                >
                  アシスタントを呼ぶ
                </button>
              )}
            </div>
          </div>
          <nav className="flex gap-1">
            {NAV.map((entry) => (
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
          <AiChat knowledgeCount={counts?.confirmed ?? null} reloadKey={reloadKey} />
        </div>

        {tab !== "chat" && (
          <div className="h-full overflow-y-auto">
            <div className="mx-auto max-w-3xl px-4 py-8">
              {tab === "input" && (
                <KnowledgeInput onCreated={onChanged} note={inputHandoff?.note ?? null} />
              )}
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
