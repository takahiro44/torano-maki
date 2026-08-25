/**
 * 構造化フィールドを項目ごとに表示する。search_text は出さない。
 */

import type { Knowledge } from "../types/api";
import { CBR_FIELD_LABELS } from "../types/api";

type Props = {
  knowledge: Knowledge;
  /** 空欄も出す。一覧での格納確認用 */
  showEmpty?: boolean;
};

export function KnowledgeArticle({ knowledge, showEmpty = false }: Props) {
  return (
    <div className="space-y-3">
      <dl className="space-y-3">
        {CBR_FIELD_LABELS.map(({ key, label }) => {
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
    </div>
  );
}
