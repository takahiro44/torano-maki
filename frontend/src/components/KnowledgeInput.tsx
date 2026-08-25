/**
 * 自由テキストを受け取り、LLM抽出して生文+構造化をまとめて登録する。
 *
 * 分類フォームは出さない。入力の手間を増やさないことが価値のため。
 */

import { useState } from "react";
import { ingestText } from "../api/client";

const EXAMPLES = [
  "先日、田中製作所様との商談で、他社より価格が高いと指摘されました。すぐに値引きせず、比較ポイントを何にされているか聞いたところ、保守対応の質を重視していることが分かりました。価格以外の価値、特に24時間対応と現地エンジニアの体制を説明したところ、最終的に受注できました。価格反論には、値引きより先に評価軸を確認するのが有効だと学びました。",
  "今日は複数の気づきがあった一日でした。まず、A社の山田部長への訪問では、朝一の時間帯だと機嫌が良く、提案を受け入れてもらいやすいことが分かりました。また、製造業のお客様全般に言えることですが、決算月の1-2ヶ月前に設備投資の意向を必ず確認すべきです。9月と3月に予算執行が集中するので、この時期を逃すと他社に取られます。",
  "B商事様の担当が来月から変わるらしい。前任者との関係性を新任者にも引き継がないと。",
];

type Props = { onCreated: () => void };

export function KnowledgeInput({ onCreated }: Props) {
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(
    null,
  );

  async function submit() {
    const text = content.trim();
    if (!text) return;
    setSaving(true);
    setMessage(null);
    try {
      const result = await ingestText(text);
      if (result.saved.length === 0) {
        setMessage({
          kind: "error",
          text: "具体的なナレッジを抽出できませんでした。エピソードをもう少し詳しく書いてみてください。",
        });
        return;
      }
      setContent("");
      const extra = result.notes?.length ? ` ${result.notes.join(" ")}` : "";
      setMessage({
        kind: "ok",
        text: `${result.saved.length}件を構造化して登録しました。一覧・探すから確認できます。${extra}`,
      });
      onCreated();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">ナレッジを登録</h2>
        <p className="mt-1 text-sm text-slate-500">
          形式は問いません。走り書きでも、長い議事録でも構いません。AIが構造化します。
          登録直後は下書きです。一覧で「承認」すると検索対象になります。上限は10万文字です。
        </p>
      </div>

      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void submit();
        }}
        rows={10}
        placeholder="例）先日の商談で価格が高いと言われたので、いきなり値引きせず…"
        className="w-full resize-y rounded-lg border border-slate-300 bg-white p-3 text-sm
                   leading-relaxed outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
      />

      <div className="flex items-center gap-3">
        <button
          onClick={() => void submit()}
          disabled={saving || !content.trim()}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                     hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {saving ? "抽出して登録中…" : "抽出して登録する"}
        </button>
        <span className="text-xs text-slate-400">Ctrl + Enter でも登録できます</span>
      </div>

      {message && (
        <p
          className={
            message.kind === "ok" ? "text-sm text-emerald-700" : "text-sm text-red-700"
          }
        >
          {message.text}
        </p>
      )}

      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <p className="text-xs font-medium text-slate-500">
          デモ用の原文（クリックで入力欄に入ります）
        </p>
        <ul className="mt-2 space-y-2">
          {EXAMPLES.map((ex, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => setContent(ex)}
                className="w-full rounded-md px-2 py-1.5 text-left text-sm leading-relaxed
                           text-slate-600 hover:bg-slate-50 hover:text-slate-900"
              >
                <span className="mr-2 text-xs font-medium text-slate-400">例{i + 1}</span>
                {ex.length > 80 ? `${ex.slice(0, 80)}…` : ex}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
