/**
 * ナレッジの入力画面。
 *
 * 入力時に構造化を強制しない（実装計画 §4）。
 * 分類やタイトルを求めず、雑な文章をそのまま受け取ることを優先する。
 * 入力の手間を増やさないことが本プロダクトの中心的な価値のため。
 */

import { useState } from "react";
import { createKnowledge } from "../api/client";

const EXAMPLES = [
  "A社大阪支社は価格よりも導入後のサポート体制を重視する傾向がある",
  "製造業の顧客には稼働停止のリスク低減を軸に提案すると刺さりやすい",
  "値引き交渉に入る前に、まず導入後の運用コスト削減額を示すと話が早い",
];

type Props = { onCreated: () => void };

export function KnowledgeInput({ onCreated }: Props) {
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  async function submit() {
    const text = content.trim();
    if (!text) return;
    setSaving(true);
    setMessage(null);
    try {
      await createKnowledge(text);
      setContent("");
      setMessage({ kind: "ok", text: "登録しました" });
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
          形式は自由です。商談で気づいたことをそのまま書いてください。
        </p>
      </div>

      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        // Ctrl/Cmd + Enter で送信。営業が素早く書き捨てられるようにするため
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
        }}
        rows={5}
        placeholder="例）A社の担当者は導入後のサポート体制を気にしていた"
        className="w-full resize-y rounded-lg border border-slate-300 bg-white p-3 text-sm
                   outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
      />

      <div className="flex items-center gap-3">
        <button
          onClick={submit}
          disabled={saving || !content.trim()}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                     hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {saving ? "登録中…" : "登録する"}
        </button>
        <span className="text-xs text-slate-400">Ctrl + Enter でも登録できます</span>
      </div>

      {message && (
        <p
          className={
            message.kind === "ok"
              ? "text-sm text-emerald-700"
              : "text-sm text-red-700"
          }
        >
          {message.text}
        </p>
      )}

      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <p className="text-xs font-medium text-slate-500">
          試すデータがないとき（クリックで入力欄に入ります）
        </p>
        <ul className="mt-2 space-y-1">
          {EXAMPLES.map((ex) => (
            <li key={ex}>
              <button
                onClick={() => setContent(ex)}
                className="text-left text-sm text-slate-600 underline decoration-slate-300
                           underline-offset-2 hover:text-slate-900"
              >
                {ex}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
