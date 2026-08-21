/**
 * ナレッジの一覧画面。編集と削除もここで行う。
 *
 * 削除は論理削除なので、押しても復帰できる。
 * ただし利用者にはそれが分からないため、確認してから実行する。
 */

import { useEffect, useState } from "react";
import { deleteKnowledge, listKnowledge, updateKnowledge } from "../api/client";
import type { Knowledge } from "../types/api";

type Props = {
  reloadKey: number;
  /** 編集・削除で件数が変わったことを親に伝える。
   *  子の内部状態だけで再取得すると、ヘッダーの件数が古いまま残るため。 */
  onChanged: () => void;
};

export function KnowledgeList({ reloadKey, onChanged }: Props) {
  const [items, setItems] = useState<Knowledge[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  // 再取得は親に一本化する。ここで独自に再取得すると、
  // 一覧は更新されてもヘッダーの件数が古いまま残る
  const refresh = onChanged;

  useEffect(() => {
    // 連続で操作したとき、先に投げた古い応答が後の結果を上書きしないようにする
    let cancelled = false;
    void (async () => {
      try {
        const rows = await listKnowledge({ limit: 100 });
        if (cancelled) return;
        setItems(rows);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  async function save(id: string) {
    setBusyId(id);
    try {
      await updateKnowledge(id, { content: draft });
      setEditingId(null);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(id: string) {
    if (!window.confirm("削除しますか？")) return;
    setBusyId(id);
    try {
      await deleteKnowledge(id);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (items === null) return <p className="text-sm text-slate-500">読み込み中…</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold">登録済みナレッジ</h2>
        <span className="text-sm text-slate-500">{items.length}件</span>
      </div>

      {items.length === 0 && (
        <p className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500">
          まだありません。「登録」タブから追加してください。
        </p>
      )}

      <ul className="space-y-2">
        {items.map((k) => (
          <li key={k.id} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            {editingId === k.id ? (
              <div className="space-y-2">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={3}
                  className="w-full rounded border border-slate-300 p-2 text-sm outline-none
                             focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => save(k.id)}
                    disabled={busyId === k.id || !draft.trim()}
                    className="rounded bg-slate-900 px-3 py-1 text-xs text-white
                               hover:bg-slate-700 disabled:bg-slate-300"
                  >
                    {busyId === k.id ? "保存中…" : "保存"}
                  </button>
                  <button
                    onClick={() => setEditingId(null)}
                    className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600"
                  >
                    やめる
                  </button>
                  <span className="self-center text-xs text-slate-400">
                    本文を変えると埋め込みも作り直されます
                  </span>
                </div>
              </div>
            ) : (
              <>
                <p className="text-sm leading-relaxed">{k.content}</p>
                <div className="mt-3 flex items-center gap-3 border-t border-slate-100 pt-2 text-xs text-slate-400">
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">
                    {k.status}
                  </span>
                  <span>{new Date(k.created_at).toLocaleString("ja-JP")}</span>
                  <div className="ml-auto flex gap-2">
                    <button
                      onClick={() => {
                        setEditingId(k.id);
                        setDraft(k.content);
                      }}
                      className="text-slate-500 underline underline-offset-2 hover:text-slate-900"
                    >
                      編集
                    </button>
                    <button
                      onClick={() => remove(k.id)}
                      disabled={busyId === k.id}
                      className="text-red-600 underline underline-offset-2 hover:text-red-800"
                    >
                      削除
                    </button>
                  </div>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
