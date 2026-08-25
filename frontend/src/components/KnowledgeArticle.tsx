/**
 * 構造化フィールドの詳細。search_text は出さない。
 */

import type { Knowledge } from "../types/api";
import { DETAIL_FIELD_LABELS } from "../types/api";

type Props = {
  knowledge: Knowledge;
  showEmpty?: boolean;
};

export function KnowledgeArticle({ knowledge, showEmpty = false }: Props) {
  return (
    <dl className="space-y-3">
      {DETAIL_FIELD_LABELS.map(({ key, label }) => {
        const value = knowledge[key];
        const empty = value === null || value === undefined || String(value).trim() === "";
        if (empty && !showEmpty) {
          return null;
        }
        return (
          <div key={key}>
            <dt className="text-xs font-medium text-slate-500">{label}</dt>
            <dd
              className={
                "mt-0.5 whitespace-pre-wrap text-sm leading-relaxed " +
                (empty ? "text-slate-400" : "text-slate-800")
              }
            >
              {empty ? "（未抽出）" : String(value)}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
