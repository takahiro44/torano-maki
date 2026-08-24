/**
 * CBR フィールドを項目ごとに表示する。search_text は出さない。
 */

import type { Knowledge } from "../types/api";
import { CBR_FIELD_LABELS } from "../types/api";

type Props = {
  knowledge: Knowledge;
};

export function KnowledgeArticle({ knowledge }: Props) {
  const original = knowledge.original_content?.trim() ?? "";

  return (
    <div className="space-y-3">
      {original && (
        <section>
          <h3 className="text-xs font-medium tracking-wide text-slate-500">入力した原文</h3>
          <p className="mt-1 whitespace-pre-wrap rounded-md bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-700">
            {original}
          </p>
        </section>
      )}
      <section>
        {original && (
          <h3 className="text-xs font-medium tracking-wide text-slate-500">構造化したナレッジ（CBR）</h3>
        )}
        <dl className="mt-1 space-y-3">
          {CBR_FIELD_LABELS.map(({ key, label }) => {
            const value = knowledge[key];
            if (value === null || value === undefined || String(value).trim() === "") {
              return null;
            }
            return (
              <div key={key}>
                <dt className="text-xs font-medium text-slate-500">{label}</dt>
                <dd className="mt-0.5 whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                  {String(value)}
                </dd>
              </div>
            );
          })}
        </dl>
      </section>
    </div>
  );
}
