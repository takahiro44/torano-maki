/**
 * 上司レビューの一覧・回答画面。
 *
 * 認証・ユーザー管理を作らない方針（CLAUDE.md 3.1）のため、
 * 「上司」を個人として識別しない。送信済みのレビューはこの画面を
 * 開いた人なら誰でも見え、誰でも回答できる。
 *
 * **会話ログを一番上に置かない。** 上司が最初に読むべきは診断であって
 * 生ログではない。並びは「AIの申し送り → 理解度 → 答えてほしいこと →
 * ナレッジは有る → 会話ログ（折りたたみ） → 回答欄」の順にしてある。
 *
 * **AIが「何を教えればいいか」まで言う。** 材料（本人の自己申告と、
 * 問いごとのナレッジDB照合）は保存済みで、そこから機械的に作れる
 * （lib/reviewBriefing.ts）。上司にログを読み解かせない。
 *
 * **ここにもアシスタントを置く。** 待ち時間の長さではなく、
 * 「後輩の代理で申し送りをする役」がこの画面には要る。喋る内容は
 * 診断から組み立てた文で、LLMは呼ばない。
 *
 * **回答したあとはナレッジ登録と同じ扱いにする。** 上司の回答も、AIが
 * 構造化した下書きが出るところまでは登録画面と同じ作業である。以前は
 * ここだけ confirmed で直接登録していたため、抽出が一言外していても
 * 直す機会が無かった。抽出結果の確認・修正・承認は同じ部品を使う
 * （KnowledgeDrafts）。
 */

import { useEffect, useState } from "react";
import { getChatReview, listChatReviews, respondChatReview } from "../api/client";
import { Celebration } from "./Celebration";
import { AgentPet } from "./chat/AgentPet";
import { ExtractionProgress } from "./ExtractionProgress";
import { KnowledgeDrafts } from "./KnowledgeDrafts";
import {
  learnerLabel,
  missingQuestions,
  reachableQuestions,
  reviewBriefing,
  shakyPoints,
} from "../lib/reviewBriefing";
import type {
  ChatReviewDetail,
  ChatReviewListItem,
  Knowledge,
  ReviewQuestion,
  UnderstandingLevel,
} from "../types/api";

const SPEAKER_LABEL: Record<string, string> = {
  user: "後輩",
  assistant: "AI",
};

/** 理解度の見せ方。色は「上司がやることの重さ」に対応させる */
const LEVEL_STYLE: Record<UnderstandingLevel, { label: string; className: string }> = {
  understood: {
    label: "説明できる",
    className: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  },
  shaky: {
    label: "あやしい",
    className: "bg-amber-50 text-amber-900 ring-amber-200",
  },
  unknown: {
    label: "わかってない",
    className: "bg-rose-50 text-rose-900 ring-rose-200",
  },
};

export function SupervisorInbox() {
  const [items, setItems] = useState<ChatReviewListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ChatReviewDetail | null>(null);
  const [response, setResponse] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  // 送った回答の原文。抽出中の表示に映し、抽出後はAI相談の裏取りに渡す
  // （回答欄は成功時に空にするので、そちらは使えない）
  const [answered, setAnswered] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  // 祝いに出す件数と、ペットへの合図（KnowledgeInput と同じ作り）
  const [celebration, setCelebration] = useState<number | null>(null);
  const [cheer, setCheer] = useState(0);

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

  /** 開き直す。**前の回答の原文と結果は連れて行かない**（別の依頼の話になる） */
  function openReview(id: string) {
    setSelectedId(selectedId === id ? null : id);
    setMessage(null);
    setAnswered(null);
    setCelebration(null);
    setResponse("");
  }

  async function respond() {
    if (!selectedId || !response.trim()) return;
    setBusy(true);
    setMessage(null);
    setCelebration(null);
    setAnswered(response.trim());
    try {
      const updated = await respondChatReview(selectedId, response.trim());
      setDetail(updated);
      setResponse("");
      setReloadKey((n) => n + 1);
      const drafts = updated.created_knowledge.filter((k) => k.status === "draft");
      setMessage({
        kind: "ok",
        text: drafts.length
          ? `回答から${drafts.length}件を構造化しました。内容を確認し、必要なら直してから承認すると、AIに聞く画面の検索対象になります。`
          : "回答を保存しました。具体的なナレッジは抽出できませんでした。",
      });
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  /**
   * 承認・保存された分を手元の詳細へ返す。
   *
   * **出し切ったときだけ祝う。** 1件ごとに祝うと、まだ残っているのに
   * 終わったように見える（KnowledgeInput と同じ理由）
   */
  function applyUpdated(updated: Knowledge[]) {
    if (detail === null) return;
    const byId = new Map(updated.map((k) => [k.id, k]));
    const next = detail.created_knowledge.map((k) => byId.get(k.id) ?? k);
    setDetail({ ...detail, created_knowledge: next });
    const remaining = next.filter((k) => k.status === "draft").length;
    if (remaining === 0 && updated.some((k) => k.status === "confirmed")) {
      setCelebration(next.length);
      setCheer((n) => n + 1);
    }
  }

  if (error && items === null) return <p className="text-sm text-red-700">{error}</p>;
  if (items === null) return <p className="text-sm text-slate-500">読み込み中…</p>;

  const pending = items.filter((i) => i.status === "pending").length;
  const briefing = activeDetail ? reviewBriefing(activeDetail) : null;
  const draftKnowledge = activeDetail?.created_knowledge.filter((k) => k.status === "draft") ?? [];
  const confirmedKnowledge =
    activeDetail?.created_knowledge.filter((k) => k.status !== "draft") ?? [];

  return (
    <>
      {/* 開いていなければ一覧の隣、開いたら申し送りの隣に立つ（ANCHORS の review）。
          喋るのは診断そのもので、待ち時間の実況ではない。
          縦の並びの外に置く。fixed なので場所は取らないが、space-y の
          最初の子になると見出しに余計な余白が入る */}
      <AgentPet
        scene="review"
        phase={busy ? "searching" : confirming ? "answering" : activeDetail ? "done" : "idle"}
        foundCount={activeDetail ? activeDetail.knowledge_gaps.length : pending}
        says={briefing?.lines ?? null}
        cheer={cheer}
      />

      {/* 祝いは全面に出るが操作は素通しする（Celebration）。
          key を入れ替えて、続けて回答したときに最初から出し直す */}
      {celebration !== null && (
        <Celebration key={cheer} count={celebration} onDone={() => setCelebration(null)} />
      )}

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

        <ul data-pet-anchor="review-list" className="space-y-2">
          {items.map((item) => (
            <li key={item.id}>
              <button
                onClick={() => openReview(item.id)}
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

              {selectedId === item.id && activeDetail && briefing && (
                <div className="mt-2 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
                  {/* 1. AIの申し送り。上司がこの画面で最初に読む一段 */}
                  <section
                    data-pet-anchor="review-briefing"
                    className="rounded-lg bg-white p-3 ring-1 ring-indigo-200/70"
                  >
                    <p className="text-[11px] font-medium text-indigo-600">AIからの申し送り</p>
                    <p className="mt-1 text-sm leading-relaxed text-slate-800">{briefing.text}</p>
                  </section>

                  {/* 2. 理解度。色は上司がやることの重さに対応している */}
                  {activeDetail.understood_points.length > 0 && (
                    <section>
                      <p className="text-xs font-medium text-slate-500">
                        {learnerLabel(activeDetail)}の理解度（本人の申告）
                      </p>
                      <ul className="mt-1 flex flex-wrap gap-1.5">
                        {activeDetail.understood_points.map((p, i) => (
                          <li
                            key={i}
                            className={
                              "rounded-lg px-2.5 py-1.5 text-xs ring-1 " +
                              LEVEL_STYLE[p.level].className
                            }
                          >
                            {p.point}
                            <span className="mt-0.5 block text-[10.5px] opacity-70">
                              {LEVEL_STYLE[p.level].label}
                            </span>
                          </li>
                        ))}
                      </ul>
                      {shakyPoints(activeDetail).length > 0 && (
                        <p className="mt-1 text-[10.5px] text-slate-400">
                          「あやしい」は、言えてはいるが根拠が本人の中に無い箇所。
                          会話ログだけでは見えないので、本人に聞いてある
                        </p>
                      )}
                    </section>
                  )}

                  {/* 3. 答えを書く必要があるのはこちらだけ */}
                  <QuestionList
                    title="あなたにしか答えられないこと"
                    tone="missing"
                    questions={missingQuestions(activeDetail)}
                  />

                  {/* 4. 答えではなく、既存ナレッジの適用場面を直せば済む */}
                  <QuestionList
                    title="ナレッジには有ったが、辿り着けなかった"
                    tone="reachable"
                    questions={reachableQuestions(activeDetail)}
                  />

                  {/* 5. 古い依頼 */}
                  <QuestionList
                    title="質問（ナレッジDBとの照合前）"
                    tone="unmatched"
                    questions={activeDetail.knowledge_gaps.filter((q) => q.db_state === null)}
                  />

                  {/* 6. 生ログは既定で閉じる。読むのは診断で足りなかったときだけ */}
                  <details className="rounded-md bg-white p-2 ring-1 ring-slate-200/80">
                    <summary className="cursor-pointer text-xs font-medium text-slate-500">
                      会話ログ（{activeDetail.chat_history.length}件）
                    </summary>
                    <div className="mt-1 max-h-48 space-y-2 overflow-y-auto">
                      {activeDetail.chat_history.map((m, i) => (
                        <p key={i} className="text-xs text-slate-700">
                          <span className="font-medium text-slate-500">
                            {SPEAKER_LABEL[m.role] ?? m.role}:
                          </span>{" "}
                          {m.content}
                        </p>
                      ))}
                    </div>
                  </details>

                  {activeDetail.status === "pending" ? (
                    <div data-pet-anchor="review-answer">
                      <label className="block text-xs font-medium text-slate-500">
                        回答（AIが構造化します。承認するとナレッジになります）
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
                        {busy ? "抽出しています…" : "回答する"}
                      </button>
                    </div>
                  ) : (
                    <div data-pet-anchor="review-answer">
                      <p className="text-xs font-medium text-slate-500">上司の回答</p>
                      <p className="mt-1 text-sm text-slate-700">
                        {activeDetail.supervisor_response}
                      </p>
                      {confirmedKnowledge.length > 0 && (
                        <p className="mt-2 text-xs text-emerald-700">
                          登録されたナレッジ: {confirmedKnowledge.map((k) => k.title).join(" / ")}
                        </p>
                      )}
                    </div>
                  )}

                  {/* 抽出の待ち時間は登録画面と同じくらい長い。何をしているかを出す */}
                  {busy && answered && <ExtractionProgress text={answered} />}

                  {message && (
                    <p
                      className={
                        "rounded-xl px-3.5 py-2.5 text-sm ring-1 " +
                        (message.kind === "ok"
                          ? "bg-emerald-50 text-emerald-800 ring-emerald-200/70"
                          : "bg-rose-50 text-rose-800 ring-rose-200/70")
                      }
                    >
                      {message.text}
                    </p>
                  )}

                  {/* ここから先はナレッジ登録とまったく同じ部品。
                      回答を閉じて開き直しても、承認し残した下書きはここに出る */}
                  <KnowledgeDrafts
                    drafts={draftKnowledge}
                    sourceText={answered ?? activeDetail.supervisor_response}
                    anchor="review-drafts"
                    title="回答から抽出したナレッジ（下書き）"
                    onUpdated={applyUpdated}
                    onMessage={setMessage}
                    onConfirming={setConfirming}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

/**
 * 問いの一覧。
 *
 * **本人が書いたものに印を付ける。** 会話ログを読めば分かる疑問と、
 * 本人しか知らない引っかかりは、上司にとって価値がまるで違う。
 */
function QuestionList({
  title,
  tone,
  questions,
}: {
  title: string;
  tone: "missing" | "reachable" | "unmatched";
  questions: ReviewQuestion[];
}) {
  if (questions.length === 0) return null;

  const style =
    tone === "missing"
      ? { heading: "text-amber-700", item: "bg-amber-50 text-amber-900" }
      : tone === "reachable"
        ? {
            heading: "text-slate-500",
            item: "bg-white text-slate-700 ring-1 ring-slate-200/80",
          }
        : {
            heading: "text-slate-500",
            item: "bg-white text-slate-700 ring-1 ring-slate-200/80",
          };

  return (
    <section>
      <p className={"text-xs font-medium " + style.heading}>
        {title}（{questions.length}件）
      </p>
      <ul className="mt-1 space-y-1">
        {questions.map((q, i) => (
          <li key={i} className={"rounded-lg px-2.5 py-1.5 text-xs " + style.item}>
            <div className="flex items-start gap-1.5">
              <span className="min-w-0 flex-1">{q.question}</span>
              {q.source === "learner" && (
                <span className="shrink-0 rounded bg-indigo-100 px-1 py-0.5 text-[10px] text-indigo-700">
                  本人が書いた
                </span>
              )}
            </div>
            <ExistingKnowledge question={q} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * 当たった既存ナレッジ。
 *
 * **類似度をそのまま出す。** サーバがこの数字で判定しているため、
 * 出しておかないと上司が判定を検証できない（当たっていないのに
 * 「有る」と言われても、確かめる手がかりが無い）。
 */
function ExistingKnowledge({ question }: { question: ReviewQuestion }) {
  if (question.existing_knowledge.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {question.existing_knowledge.map((k) => (
        <li key={k.knowledge_id} className="flex items-baseline gap-1.5 text-[11px]">
          <span className="truncate text-indigo-700">{k.title}</span>
          {k.semantic_score !== null && (
            <span className="shrink-0 font-mono text-[10px] text-slate-400">
              {k.semantic_score.toFixed(2)}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
