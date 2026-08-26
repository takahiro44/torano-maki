/**
 * 上司レビューの一覧・回答画面。
 *
 * 認証・ユーザー管理を作らない方針（CLAUDE.md 3.1）のため、
 * 「上司」を個人として識別しない。送信済みのレビューはこの画面を
 * 開いた人なら誰でも見え、誰でも回答できる。
 */

import { useEffect, useState } from "react";
import { getChatReview, listChatReviews, respondChatReview } from "../api/client";
import type { ChatReviewDetail, ChatReviewListItem } from "../types/api";

const SPEAKER_LABEL: Record<string, string> = {
  user: "後輩",
  assistant: "AI",
};

export function SupervisorInbox() {
  const [items, setItems] = useState<ChatReviewListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ChatReviewDetail | null>(null);
  const [response, setResponse] = useState("");
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rows = await listChatReviews();
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

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    void (async () => {
      try {
        const d = await getChatReview(selectedId);
        if (!cancelled) setDetail(d);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  // detail.id で照合する。選択を素早く切り替えたとき、遅れて返ってきた
  // 古いfetchの結果を新しい選択に対して出さないための最終防御
  const activeDetail = selectedId && detail?.id === selectedId ? detail : null;

  async function respond() {
    if (!selectedId || !response.trim()) return;
    setBusy(true);
    try {
      const updated = await respondChatReview(selectedId, response.trim());
      setDetail(updated);
      setResponse("");
      setReloadKey((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (error && items === null) return <p className="text-sm text-red-700">{error}</p>;
  if (items === null) return <p className="text-sm text-slate-500">読み込み中…</p>;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">上司レビュー</h2>
        <p className="mt-1 text-xs text-slate-500">
          後輩がAIチャットで解決できなかった疑問に回答すると、confirmedのナレッジとして登録されます。
        </p>
      </div>

      {error && <p className="text-sm text-red-700">{error}</p>}

      {items.length === 0 && (
        <p className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500">
          レビュー依頼はまだありません。
        </p>
      )}

      <ul className="space-y-2">
        {items.map((item) => (
          <li key={item.id}>
            <button
              onClick={() => setSelectedId(selectedId === item.id ? null : item.id)}
              className="w-full rounded-lg border border-slate-200 bg-white p-3 text-left text-sm hover:border-slate-300"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-slate-800">{item.summary}</span>
                <span
                  className={
                    "shrink-0 rounded px-1.5 py-0.5 text-xs " +
                    (item.status === "pending"
                      ? "bg-amber-100 text-amber-800"
                      : "bg-emerald-100 text-emerald-800")
                  }
                >
                  {item.status === "pending" ? "未回答" : "回答済み"}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-400">
                {new Date(item.created_at).toLocaleString("ja-JP")}
              </p>
            </button>

            {selectedId === item.id && activeDetail && (
              <div className="mt-2 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div>
                  <p className="text-xs font-medium text-slate-500">会話ログ</p>
                  <div className="mt-1 max-h-48 space-y-2 overflow-y-auto rounded-md bg-white p-2 ring-1 ring-slate-200/80">
                    {activeDetail.chat_history.map((m, i) => (
                      <p key={i} className="text-xs text-slate-700">
                        <span className="font-medium text-slate-500">
                          {SPEAKER_LABEL[m.role] ?? m.role}:
                        </span>{" "}
                        {m.content}
                      </p>
                    ))}
                  </div>
                </div>

                {activeDetail.knowledge_gaps.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-amber-700">ナレッジDBに不足していた点</p>
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-amber-800">
                      {activeDetail.knowledge_gaps.map((g, i) => (
                        <li key={i}>{g}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {activeDetail.status === "pending" ? (
                  <div>
                    <label className="block text-xs font-medium text-slate-500">
                      回答（そのままナレッジとして登録されます）
                    </label>
                    <textarea
                      value={response}
                      onChange={(e) => setResponse(e.target.value)}
                      rows={4}
                      className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                      placeholder="例）出荷後に在庫差異が判明したら、気づいた時点ですぐ顧客に連絡し…"
                    />
                    <button
                      onClick={() => void respond()}
                      disabled={busy || !response.trim()}
                      className="mt-2 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white
                                 hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-slate-200"
                    >
                      {busy ? "登録しています…" : "回答してナレッジ化する"}
                    </button>
                  </div>
                ) : (
                  <div>
                    <p className="text-xs font-medium text-slate-500">上司の回答</p>
                    <p className="mt-1 text-sm text-slate-700">{activeDetail.supervisor_response}</p>
                    {activeDetail.created_knowledge.length > 0 && (
                      <p className="mt-2 text-xs text-emerald-700">
                        登録されたナレッジ:{" "}
                        {activeDetail.created_knowledge.map((k) => k.title).join(" / ")}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
