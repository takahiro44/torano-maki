/**
 * ナレッジの一覧。登録画面で承認しそびれた下書きの確認・編集用。
 * 空欄の CBR も出して、DB に何が入ったかを確認できるようにする。
 */

import { useEffect, useState } from "react";
import { deleteKnowledge, listKnowledge, updateKnowledge } from "../api/client";
import type { Knowledge, KnowledgeSortField, SortDirection } from "../types/api";
import { CBR_FIELD_LABELS } from "../types/api";
import { KnowledgeCard } from "./KnowledgeCard";

type Props = {
  reloadKey: number;
  onChanged: () => void;
};

type CbrDraft = {
  title: string;
  situation: string;
  problem: string;
  judgment: string;
  action: string;
  reasoning: string;
  outcome: string;
  lesson: string;
  applicable_situations: string;
  limitations: string;
  industry: string;
  product: string;
  sales_stage: string;
};

const SORT_OPTIONS: { value: KnowledgeSortField; label: string }[] = [
  { value: "created_at", label: "登録日時" },
  { value: "updated_at", label: "更新日時" },
  { value: "title", label: "タイトル" },
  { value: "status", label: "ステータス" },
];

function toDraft(k: Knowledge): CbrDraft {
  return {
    title: k.title,
    situation: k.situation ?? "",
    problem: k.problem ?? "",
    judgment: k.judgment ?? "",
    action: k.action ?? "",
    reasoning: k.reasoning ?? "",
    outcome: k.outcome ?? "",
    lesson: k.lesson ?? "",
    applicable_situations: k.applicable_situations ?? "",
    limitations: k.limitations ?? "",
    industry: k.industry ?? "",
    product: k.product ?? "",
    sales_stage: k.sales_stage ?? "",
  };
}

export function KnowledgeList({ reloadKey, onChanged }: Props) {
  const [items, setItems] = useState<Knowledge[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [sort, setSort] = useState<KnowledgeSortField>("created_at");
  const [order, setOrder] = useState<SortDirection>("desc");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<CbrDraft | null>(null);

  const refresh = onChanged;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rows = await listKnowledge({ limit: 100, sort, order });
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
  }, [reloadKey, sort, order]);

  function startEdit(k: Knowledge) {
    setEditingId(k.id);
    setDraft(toDraft(k));
  }

  async function saveEdit(id: string) {
    if (!draft || !draft.title.trim()) {
      setError("タイトルは必須です");
      return;
    }
    setBusyId(id);
    try {
      await updateKnowledge(id, {
        title: draft.title.trim(),
        situation: draft.situation,
        problem: draft.problem,
        judgment: draft.judgment,
        action: draft.action,
        reasoning: draft.reasoning,
        outcome: draft.outcome,
        lesson: draft.lesson,
        applicable_situations: draft.applicable_situations,
        limitations: draft.limitations,
        industry: draft.industry,
        product: draft.product,
        sales_stage: draft.sales_stage,
      });
      setEditingId(null);
      setDraft(null);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

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
      if (editingId === id) {
        setEditingId(null);
        setDraft(null);
      }
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  if (error && items === null) return <p className="text-sm text-red-700">{error}</p>;
  if (items === null) return <p className="text-sm text-slate-500">読み込み中…</p>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">登録済みナレッジ</h2>
          <p className="mt-1 text-xs text-slate-500">
            タイトルと要約だけ出します。詳細と根拠の原文は「詳細を見る」から。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-slate-500">{items.length}件</span>
          <label className="flex items-center gap-1 text-slate-600">
            並び
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as KnowledgeSortField)}
              className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
            >
              {SORT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <select
            value={order}
            onChange={(e) => setOrder(e.target.value as SortDirection)}
            className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
          >
            <option value="desc">降順</option>
            <option value="asc">昇順</option>
          </select>
        </div>
      </div>

      {error && <p className="text-sm text-red-700">{error}</p>}

      {items.length === 0 && (
        <p className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500">
          まだありません。「登録」タブから追加してください。
        </p>
      )}

      <ul className="space-y-2">
        {items.map((k) => (
          <li key={k.id}>
            {editingId === k.id && draft ? (
              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  void saveEdit(k.id);
                }}
              >
                {CBR_FIELD_LABELS.map(({ key, label }) => (
                  <label key={key} className="block">
                    <span className="text-xs font-medium text-slate-500">{label}</span>
                    {key === "title" ? (
                      <input
                        value={draft.title}
                        onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                        className="mt-0.5 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                        required
                      />
                    ) : (
                      <textarea
                        value={draft[key as keyof CbrDraft]}
                        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                        rows={3}
                        className="mt-0.5 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                      />
                    )}
                  </label>
                ))}
                <div className="flex gap-2">
                  <button
                    type="submit"
                    disabled={busyId === k.id}
                    className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-700"
                  >
                    保存
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(null);
                      setDraft(null);
                    }}
                    className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                  >
                    キャンセル
                  </button>
                </div>
              </form>
              </div>
            ) : (
              <KnowledgeCard
                knowledge={k}
                showEmptyDetails
                extra={
                  <>
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{k.status}</span>
                    <span>{new Date(k.created_at).toLocaleString("ja-JP")}</span>
                  </>
                }
                actions={
                  <>
                    <button
                      onClick={() => startEdit(k)}
                      disabled={busyId === k.id}
                      className="text-slate-700 underline underline-offset-2 hover:text-slate-900"
                    >
                      編集
                    </button>
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
                  </>
                }
              />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
