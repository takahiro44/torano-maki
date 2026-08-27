/**
 * 振り返りが残っている練習の一覧。
 *
 * **「もう一度やる場面を選ぶ」（`PastScenes`）とは役割が違う。**
 * あちらは次の練習へ入るための入口で、こちらは終えた練習を読み返すための記録である。
 * 同じ一覧に混ぜると、押したときに何が起きるのかが行ごとに変わってしまう。
 *
 * **振り返り済みだけを出す。** 途中で画面を離れた練習まで並べると、
 * 読み返す価値のない行で埋まる。絞り込みはサーバ側（`reviewed_only`）で行う。
 * 受け取ってから捨てると、取った件数のうち何件残るか分からず、
 * 一覧が理由もなく空になる。
 *
 * 押したら振り返り画面へ移る。何を描くかは `Roleplay.tsx` がURLから決める。
 * ここで描き分けを持つと、同じ判断が2箇所に増えて静かに食い違う。
 */

import { useEffect, useState } from "react";
import { listRoleplaySessions } from "../../api/client";
import { navigate, roleplaySessionPath } from "../../lib/router";
import type { RoleplaySessionSummary } from "../../types/api";

/** 読み返すのはせいぜい直近の数件なので絞る */
const HISTORY_LIMIT = 10;

export function RoleplayHistory() {
  const [items, setItems] = useState<RoleplaySessionSummary[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listRoleplaySessions({ limit: HISTORY_LIMIT, reviewedOnly: true })
      .then((next) => {
        if (!cancelled) setItems(next);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 取れなかった場合と0件は出さない。ここが空でも練習は始められるため、
  // 開始手段の下に空の見出しだけが残る方が邪魔になる
  if (failed || items === null || items.length === 0) return null;

  return (
    <section>
      <h3 className="text-sm font-medium text-slate-700">終えた練習を読み返す</h3>
      <p className="mt-1 text-xs text-slate-500">
        振り返りまで終わった練習です。指摘と「次に試す一言」をもう一度見られます。
      </p>
      <ul
        className="mt-2 divide-y divide-slate-100 overflow-hidden rounded-lg border
                   border-slate-200 bg-white"
      >
        {items.map((item) => (
          <li key={item.session_id}>
            <button
              onClick={() => navigate(roleplaySessionPath(item.session_id))}
              className="w-full px-4 py-3 text-left hover:bg-slate-50"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-slate-800">{item.title}</span>
                {item.category_label && (
                  <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] text-indigo-700">
                    {item.category_label}
                  </span>
                )}
                {/* 同じ場面を繰り返した記録は見出しが同じになる。
                    何回目かを出さないと、どれがいつの挑戦か分からない */}
                {item.attempt_no > 1 && (
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                    {item.attempt_no}回目
                  </span>
                )}
              </div>
              <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
                <span>{new Date(item.created_at).toLocaleString("ja-JP")}</span>
                <span>回答{item.learner_turns_used}回</span>
                {/* 見出しは場面の名前でしかない。何を聞きたくて始めたのかは
                    本人が打った文にしか残らないので、併せて出す */}
                {item.query !== item.title && (
                  <span className="truncate text-slate-500">{item.query}</span>
                )}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
