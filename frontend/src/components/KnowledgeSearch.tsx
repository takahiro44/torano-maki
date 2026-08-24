/**
 * ナレッジの検索画面。
 *
 * スコアは表示するが、**しきい値で足切りしない**。
 * e5の類似度は0.78〜0.88の狭い範囲に固まり、無関係な項目でも0.78程度は出るため、
 * 絶対値に意味があるように見せると誤解を招く（docs/setup-notes.md）。
 * そのため「参考値」と明記し、順位で読ませる。
 */

import { useState } from "react";
import { searchKnowledge } from "../api/client";
import type { KnowledgeSearchResult } from "../types/api";
import { KnowledgeArticle } from "./KnowledgeArticle";

const EXAMPLE_QUERIES = [
  "サポート体制を重視する顧客",
  "値引きを求められたら",
  "工場を持つ会社への売り方",
];

export function KnowledgeSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(q: string) {
    const text = q.trim();
    if (!text) return;
    setSearching(true);
    setError(null);
    try {
      setResults(await searchKnowledge(text, 5));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResults(null);
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">ナレッジを探す</h2>
        <p className="mt-1 text-sm text-slate-500">
          キーワードではなく、聞きたいことをそのまま書いてください。
        </p>
      </div>

      <div className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run(query)}
          placeholder="例）A社に提案するときの注意点は？"
          className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm
                     outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
        />
        <button
          onClick={() => run(query)}
          disabled={searching || !query.trim()}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                     hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {searching ? "検索中…" : "検索"}
        </button>
      </div>

      <div className="flex flex-wrap gap-2">
        {EXAMPLE_QUERIES.map((q) => (
          <button
            key={q}
            onClick={() => {
              setQuery(q);
              run(q);
            }}
            className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs
                       text-slate-600 hover:border-slate-400 hover:text-slate-900"
          >
            {q}
          </button>
        ))}
      </div>

      {error && <p className="text-sm text-red-700">{error}</p>}

      {results && (
        <div className="space-y-3">
          {/* 関連がなくても top_k 件返す仕様。利用者が「関連情報がある」と
              誤解しないよう明記する。スコアは0.78〜0.88に固まり、無関係でも
              0.78程度出るため、しきい値で足切りしていない */}
          <p className="text-xs text-slate-500">
            {results.length}件（関連が高い順）。
            <span className="ml-1">
              該当が無い場合も、登録済みデータから近い順に表示します。
              スコアは参考値なので、値ではなく順番で見てください。
            </span>
          </p>

          {results.length === 0 && (
            <p className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500">
              まだナレッジが登録されていません。「登録」タブから追加してください。
            </p>
          )}

          {results.map((r, i) => (
            <div
              key={r.id}
              className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
            >
              <div className="flex items-start gap-3">
                <span className="mt-0.5 shrink-0 rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <KnowledgeArticle knowledge={r} />
                </div>
              </div>
              <div className="mt-3 flex items-center gap-3 border-t border-slate-100 pt-2 text-xs text-slate-400">
                <span>スコア {r.score.toFixed(3)}</span>
                {/* 出典。CLAUDE.md 6章で検索結果に必須としている */}
                <span>
                  出典:{" "}
                  {r.source_id ? `${r.source_type} / ${r.source_id.slice(0, 8)}` : "手入力"}
                </span>
                <span>{new Date(r.created_at).toLocaleString("ja-JP")}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
