/**
 * 自由テキストを受け取り、LLM抽出して生文+構造化をまとめて登録する。
 *
 * 分類フォームは出さない。入力の手間を増やさないことが価値のため。
 * 承認は一覧へ行かず、この画面で完了できるようにする。
 */

import { useState } from "react";
import { ingestText, updateKnowledge } from "../api/client";
import type { Knowledge } from "../types/api";
import { KnowledgeCard } from "./KnowledgeCard";

const EXAMPLES = [
  "先日、田中製作所様との商談で、他社より価格が高いと指摘されました。すぐに値引きせず、「他社の製品とどの点を比較されていますか？」と聞きました。保守対応の質を重視していると分かったので、24時間対応と現地エンジニア常駐を説明したところ、価格差を理解いただき受注できました。",
  "B商事の担当が来月から交代。前任との関係を新任に引き継がないと、更新の話が止まる。",
  "A社の初回訪問で、標準プラン360万円が年間予算300万円を超えると言われた。その場で値引きせず、営業部30名だけの段階導入を出した。「稟議しやすい」と言われ、次回は情シス入りの見積になった。",
];

type Props = { onCreated: () => void };

export function KnowledgeInput({ onCreated }: Props) {
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState<Knowledge[]>([]);
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
        setPending([]);
        setMessage({
          kind: "error",
          text: "具体的なナレッジを抽出できませんでした。エピソードをもう少し詳しく書いてみてください。",
        });
        return;
      }
      setContent("");
      setPending(result.saved);
      const extra = result.notes?.length ? ` ${result.notes.join(" ")}` : "";
      setMessage({
        kind: "ok",
        text: `${result.saved.length}件を構造化しました。内容を確認して承認すると、「探す」の対象になります。${extra}`,
      });
      onCreated();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  async function confirmIds(ids: string[]) {
    if (ids.length === 0) return;
    setConfirming(true);
    setMessage(null);
    try {
      const updated: Knowledge[] = [];
      for (const id of ids) {
        updated.push(await updateKnowledge(id, { status: "confirmed" }));
      }
      const confirmedIds = new Set(updated.map((k) => k.id));
      setPending((prev) => prev.filter((k) => !confirmedIds.has(k.id)));
      setMessage({
        kind: "ok",
        text: `${updated.length}件を承認しました。「探す」から検索できます。`,
      });
      onCreated();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setConfirming(false);
    }
  }

  const drafts = pending.filter((k) => k.status === "draft");

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">ナレッジを登録</h2>
        <p className="mt-1 text-sm text-slate-500">
          形式は問いません。走り書きでも、長い議事録でも構いません。AIが構造化します。
          抽出後、この画面で内容を確認して承認すると検索対象になります。上限は10万文字です。
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
          disabled={saving || confirming || !content.trim()}
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

      {drafts.length > 0 && (
        <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-slate-800">抽出結果（下書き）</h3>
            <button
              type="button"
              onClick={() => void confirmIds(drafts.map((k) => k.id))}
              disabled={confirming}
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white
                         hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {confirming ? "承認中…" : "この内容で承認する"}
            </button>
          </div>
          <p className="text-xs text-slate-500">
            詳細を見て根拠の原文も確認できます。承認しない場合は下書きのまま残ります。
          </p>
          {drafts.map((k) => (
            <KnowledgeCard
              key={k.id}
              knowledge={k}
              showEmptyDetails
              extra={<span className="rounded bg-white px-1.5 py-0.5 text-slate-600">{k.status}</span>}
              actions={
                <button
                  type="button"
                  onClick={() => void confirmIds([k.id])}
                  disabled={confirming}
                  className="text-slate-700 underline underline-offset-2 hover:text-slate-900"
                >
                  承認する
                </button>
              }
            />
          ))}
        </div>
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
