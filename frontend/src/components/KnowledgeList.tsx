/**
 * ナレッジの一覧。抽出直後は draft なので、承認して検索対象にする。
 */

import { useEffect, useState } from "react";
import { deleteKnowledge, listKnowledge, updateKnowledge } from "../api/client";
import type { Knowledge } from "../types/api";
import { KnowledgeArticle } from "./KnowledgeArticle";

type Props = {
  reloadKey: number;
  onChanged: () => void;
};

export function KnowledgeList({ reloadKey, onChanged }: Props) {
  const [items, setItems] = useState<Knowledge[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = onChanged;

  useEffect(() => {
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

  async function confirm(id: string) {
    setBusyId(id);
    try {
      await updateKnowledge(id, { status: "confirmed" });
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
            <KnowledgeArticle knowledge={k} />
            <div className="mt-3 flex items-center gap-3 border-t border-slate-100 pt-2 text-xs text-slate-400">
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{k.status}</span>
              <span>{new Date(k.created_at).toLocaleString("ja-JP")}</span>
              <div className="ml-auto flex gap-2">
                {k.status === "draft" && (
                  <button
                    onClick={() => void confirm(k.id)}
                    disabled={busyId === k.id}
                    className="text-slate-700 underline underline-offset-2 hover:text-slate-900"
                  >
                    承認して検索対象にする
                  </button>
                )}
                <button
                  onClick={() => void remove(k.id)}
                  disabled={busyId === k.id}
                  className="text-red-600 underline underline-offset-2 hover:text-red-800"
                >
                  削除
                </button>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
