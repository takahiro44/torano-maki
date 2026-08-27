/**
 * 過去のロープレから選ぶ。
 *
 * **誰かが一度作った場面を、チームの誰でもそのまま練習できるようにする。**
 * 先輩が作った良い場面が、その人の1回きりで消えるのがもったいない。
 * 認証を作らない方針（CLAUDE.md 3.1）のため、ここに並ぶのは同じDBを見ている
 * 全員分である。「自分の履歴」として見せないこと。
 *
 * **押した瞬間に練習が始まる。** シナリオは過去のセッションに
 * スナップショットで残っているため、作り直す必要がない
 * （`docs/decisions.md` 2026-08-26「シナリオはJSONBのスナップショット」）。
 * 場面ボタンから始めると検索とシナリオ生成で30秒以上待つが、こちらは待たない。
 * 根拠にしたナレッジも一緒に引き継がれるので、出典が消えることもない。
 *
 * **同じ場面かどうかは見出しではなく根拠ナレッジで判断する。**
 * 見出しはLLMが書くため、同じ事例から作った場面でも
 * 「段階的改善案の提示」「段階的導入の提案」のように一言一句は揃わない。
 * 見出しで比べると、同じ練習が別カードとして際限なく増えていく。
 *
 * 一覧が取れなくても場面ボタンからの開始は試せる必要があるため、
 * 失敗は小さく出すだけにする。
 */

import { useEffect, useState } from "react";
import { listRoleplaySessions, retryRoleplay } from "../../api/client";
import type { RoleplaySession, RoleplaySessionSummary } from "../../types/api";

/**
 * まとめる前に取るセッション数（APIの上限）。
 *
 * 重複を除くと数分の1になるため、出したい枚数より大幅に多く引く。
 * LLMを呼ばない一覧なので、多めに取っても待たされない。
 */
const FETCH_LIMIT = 100;

/** 最初に見せる枚数。これを超えた分は「すべて表示」で開く */
const INITIAL_SCENES = 6;

type Scene = {
  /** この場面を代表するセッション。最新の1件から始める */
  session: RoleplaySessionSummary;
  /** チームがこの場面を練習した回数 */
  practiced: number;
};

/**
 * 根拠ナレッジごとに1場面へまとめる。並び順（新しい順）はそのまま。
 *
 * `primary_knowledge_id` が無い場合だけ `root_session_id` で代用する。
 * 出典を失った古い記録を、全部まとめて1枚にしてしまわないため。
 */
function toScenes(items: RoleplaySessionSummary[]): Scene[] {
  const scenes = new Map<string, Scene>();
  for (const session of items) {
    const key = session.primary_knowledge_id ?? session.root_session_id;
    const found = scenes.get(key);
    if (found) found.practiced += 1;
    else scenes.set(key, { session, practiced: 1 });
  }
  return [...scenes.values()];
}

type Props = {
  onStarted: (session: RoleplaySession) => void;
};

export function PastScenes({ onStarted }: Props) {
  const [scenes, setScenes] = useState<Scene[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  // どのカードを押したかを持つ。押した本人だけを「開始中」にするため
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listRoleplaySessions({ limit: FETCH_LIMIT })
      .then((items) => {
        if (!cancelled) setScenes(toScenes(items));
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function start(sessionId: string) {
    if (starting !== null) return;
    setError(null);
    setStarting(sessionId);
    try {
      // 同じシナリオの複製。LLMを呼ばないので待ち時間がない
      onStarted(await retryRoleplay(sessionId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(null);
    }
  }

  if (failed || (scenes !== null && scenes.length === 0)) return null;

  const shown = scenes === null ? [] : expanded ? scenes : scenes.slice(0, INITIAL_SCENES);
  const hidden = scenes === null ? 0 : scenes.length - shown.length;

  return (
    <section>
      <h3 className="text-sm font-medium text-slate-700">過去のロープレから選ぶ</h3>
      <p className="mt-1 text-xs text-slate-500">
        誰かが作った場面をそのまま練習できます。生成を待たずにすぐ始まります。
      </p>

      {error && (
        <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      )}

      {scenes === null ? (
        <p className="mt-2 text-xs text-slate-400">読み込んでいます…</p>
      ) : (
        <>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {shown.map(({ session, practiced }) => (
              <button
                key={session.session_id}
                onClick={() => void start(session.session_id)}
                disabled={starting !== null}
                className="rounded-lg border border-slate-300 bg-white p-3 text-left
                           hover:border-slate-500 disabled:cursor-not-allowed
                           disabled:border-slate-200 disabled:bg-slate-50"
              >
                <span className="block text-sm font-medium text-slate-800">{session.title}</span>
                {/* どの社内事例から来た場面かを出す。先輩の商談が元になっている
                    ことが見えないと、ただのAI生成問題集に見えてしまう */}
                {session.primary_knowledge_title && (
                  <span className="mt-0.5 block truncate text-[11px] text-slate-500">
                    元の事例：{session.primary_knowledge_title}
                  </span>
                )}
                <span className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
                  {session.category_label && (
                    <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-700">
                      {session.category_label}
                    </span>
                  )}
                  {/* 何度も練習されている場面は、それだけ役に立っている目印になる */}
                  <span className="text-slate-400">
                    {starting === session.session_id
                      ? "始めています…"
                      : `チームで${practiced}回練習`}
                  </span>
                </span>
              </button>
            ))}
          </div>

          {hidden > 0 && (
            <button
              onClick={() => setExpanded(true)}
              className="mt-2 text-xs text-slate-500 underline hover:text-slate-800"
            >
              すべて表示（あと{hidden}件）
            </button>
          )}
        </>
      )}
    </section>
  );
}
