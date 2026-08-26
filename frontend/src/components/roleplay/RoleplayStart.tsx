/**
 * 練習を始める画面。
 *
 * **場面を選ぶだけで始められること**を優先している。初見の人が
 * 説明なしで1回完走できることがMVPの完了条件のため、
 * 自由入力より先にカテゴリのボタンを並べる。
 *
 * シナリオ生成に30秒以上かかる。無言で待たせると「固まった」と
 * 思われるので、何をしているかと経過秒数を出し続ける。
 */

import { useEffect, useState } from "react";
import { listRoleplayCategories, startRoleplaySession } from "../../api/client";
import type { CategoryOption, RoleplayCategory, RoleplaySession } from "../../types/api";

type Props = {
  /** AIチャットの「この場面を練習する」から入った場合に渡る */
  knowledgeId?: string;
  initialQuery?: string;
  onStarted: (session: RoleplaySession) => void;
};

/** 待たせている間に出す文言。秒数だけだと何を待っているか分からない */
function waitingLabel(sec: number): string {
  if (sec < 6) return "似た場面のナレッジを探しています…";
  if (sec < 18) return "根拠になった発話を読み込んでいます…";
  return "練習する場面を組み立てています…";
}

export function RoleplayStart({ knowledgeId, initialQuery, onStarted }: Props) {
  const [categories, setCategories] = useState<CategoryOption[]>([]);
  const [query, setQuery] = useState(initialQuery ?? "");
  // 1往復モードはラウンドロビンのデモ用。60〜90秒で終わらせたいときに使う
  const [oneExchange, setOneExchange] = useState(false);
  const [pending, setPending] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // 対応表をフロントに持たない。増えた場面がここに自動で出る
    listRoleplayCategories()
      .then(setCategories)
      .catch(() => setCategories([]));
  }, []);

  useEffect(() => {
    if (!pending) return;
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [pending]);

  async function start(params: { category?: RoleplayCategory; query?: string }) {
    if (pending) return;
    setError(null);
    setElapsed(0);
    setPending(true);
    try {
      const session = await startRoleplaySession({
        ...params,
        knowledgeId,
        maxTurns: oneExchange ? 1 : 2,
      });
      onStarted(session);
    } catch (e) {
      setError(describeStartError(e));
    } finally {
      setPending(false);
    }
  }

  if (pending) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-6">
        <p className="flex items-center gap-2 text-sm text-slate-600">
          <span className="inline-block size-2 animate-pulse rounded-full bg-slate-400" />
          {waitingLabel(elapsed)}
          <span className="text-xs text-slate-400">{elapsed}秒</span>
        </p>
        <p className="mt-2 text-xs text-slate-400">
          社内の実際の商談から場面を作るため、30秒ほどかかります。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">ロープレ</h2>
        <p className="mt-1 text-sm text-slate-500">
          社内の実際の商談をもとに、判断が必要な一場面だけを短く練習します。
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      )}

      {knowledgeId && (
        <p className="rounded-md bg-indigo-50 px-3 py-2 text-xs text-indigo-900">
          AIチャットで参照したナレッジをもとに場面を作ります。
        </p>
      )}

      <section>
        <h3 className="text-sm font-medium text-slate-700">場面から選ぶ</h3>
        <div className="mt-2 flex flex-wrap gap-2">
          {categories.map((c) => (
            <button
              key={c.key}
              onClick={() => void start({ category: c.key })}
              className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm
                         text-slate-700 hover:border-slate-500 hover:text-slate-900"
            >
              {c.label}
            </button>
          ))}
          {categories.length === 0 && (
            <p className="text-xs text-slate-400">
              場面の一覧を取得できませんでした。バックエンドが起動しているか確認してください。
            </p>
          )}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-medium text-slate-700">練習したいことを書く</h3>
        <div className="mt-2 flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            // 日本語の変換確定Enterで送信されると、30秒待つ画面では致命的になる
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing && query.trim()) {
                void start({ query: query.trim() });
              }
            }}
            placeholder="例）値引きを求められたときの返し方を練習したい"
            className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm
                       outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
          />
          <button
            onClick={() => void start({ query: query.trim() })}
            disabled={!query.trim()}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                       hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            始める
          </button>
        </div>
      </section>

      <label className="flex items-center gap-2 text-xs text-slate-500">
        <input
          type="checkbox"
          checked={oneExchange}
          onChange={(e) => setOneExchange(e.target.checked)}
          className="rounded border-slate-300"
        />
        1往復で終える（短時間で試したいとき）
      </label>
    </div>
  );
}

/** 原因を推測しやすい文言にする。使う人の操作ミスではない場合が多い */
function describeStartError(e: unknown): string {
  const message = e instanceof Error ? e.message : String(e);
  if (e instanceof Error && e.name === "TimeoutError") {
    return "場面の生成が終わりませんでした。AIサーバが混んでいる可能性があります。もう一度お試しください。";
  }
  if (message.includes("根拠")) {
    // 422。ナレッジ側の準備不足なので、何をすればよいかまで書く
    return `${message}（「音声」タブから商談を取り込み、「一覧」で confirmed にすると練習できます）`;
  }
  if (message.includes("未設定")) {
    return "AIサーバが設定されていません。.env の BASE_URL / MODEL_NAME を確認してください。";
  }
  if (message.includes("接続できません")) {
    return "AIサーバ（DGX）に接続できません。起動しているか確認してください。";
  }
  return message;
}
