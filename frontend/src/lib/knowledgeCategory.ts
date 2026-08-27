/**
 * knowledge_type のバッジ表示(色分け)。
 *
 * 値の正はバックエンド(app/models/knowledge.py の KnowledgeCategory)。
 * 未知の値が来ても崩れないよう、フォールバックを用意する
 * (移行前の値やDB直編集を弾かないため)。
 */

export type KnowledgeCategoryBadge = { label: string; className: string };

const BADGES: Record<string, KnowledgeCategoryBadge> = {
  business: { label: "営業", className: "bg-indigo-50 text-indigo-600 ring-1 ring-indigo-200" },
  casual: { label: "その他", className: "bg-amber-50 text-amber-700 ring-1 ring-amber-200" },
};

export function knowledgeCategoryBadge(knowledgeType: string): KnowledgeCategoryBadge {
  return (
    BADGES[knowledgeType] ?? {
      label: knowledgeType,
      className: "bg-slate-100 text-slate-500 ring-1 ring-slate-200",
    }
  );
}
