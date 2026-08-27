/**
 * 抽出された下書きを確認・修正・承認する一段。
 *
 * **ナレッジ登録と上司レビューで同じものを使う。** どちらも「文章を投げると
 * AIが構造化し、人が見て直してから承認する」という同じ作業で、違うのは
 * 元の文章がメモか上司の回答かだけである。片方だけに「手で直す」や
 * 「AIに相談」があると、同じ抽出結果なのに直せる画面と直せない画面が生まれる。
 *
 * **承認の前に直せる。** 抽出は当たっているのに一言だけ違う、という状態が
 * 一番多い。「承認する」しか出せないと、直したい人は承認してから別の場所で
 * 直すか、捨ててもう一度書くことになる（KnowledgeEditor）。
 *
 * **祝うかどうかはここで決めない。** 出し切った合図（`onUpdated`）だけを返し、
 * 何件で祝うかは画面側が持つ。登録画面と上司レビューでは「1回の作業」の
 * 単位が違うため、ここで数えると片方が必ずずれる。
 */

import { useState } from "react";
import { updateKnowledge } from "../api/client";
import type { Knowledge } from "../types/api";
import { AiConsultBar, KnowledgeEditor } from "./KnowledgeEditor";
import { KnowledgeCard } from "./KnowledgeCard";

/** 抽出結果が1枚ずつ現れる間隔。速いと一斉に出て、遅いと待たされる */
const CARD_STAGGER_MS = 90;

type Props = {
  /** status が draft のものだけ渡すこと。承認済みは呼び出し側で外す */
  drafts: Knowledge[];
  /** 抽出の元になった原文。AI相談の裏取りに渡す */
  sourceText: string | null;
  /**
   * 承認・保存された分。**まとめて返す。**
   * 「すべて承認する」で1件ずつ返すと、呼び出し側は残り件数を数え直せない
   */
  onUpdated: (updated: Knowledge[]) => void;
  onMessage: (message: { kind: "ok" | "error"; text: string }) => void;
  /** AI相談の最中。アシスタントの機嫌に使う */
  onAiBusy?: (busy: boolean) => void;
  /** 承認の最中。画面側は入力を止め、アシスタントの機嫌にも使う */
  onConfirming?: (busy: boolean) => void;
  /** アシスタントが寄ってくる目印（AgentPet の ANCHORS） */
  anchor?: string;
  title?: string;
  hint?: string;
};

export function KnowledgeDrafts({
  drafts,
  sourceText,
  onUpdated,
  onMessage,
  onAiBusy,
  onConfirming,
  anchor,
  title = "抽出結果（下書き）",
  hint = "詳細を開くと根拠の原文も確認できます。承認しない場合は下書きのまま残ります。",
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  // 編集フォームを開くと同時にAIへ投げる指示。カードのボタンから直接相談するため
  const [autoConsult, setAutoConsult] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  if (drafts.length === 0) return null;

  /** カードから直接AIへ相談する。指示が空なら編集フォームを開くだけ */
  function askAi(id: string, instruction: string) {
    setEditingId(id);
    setAutoConsult(instruction || null);
  }

  function closeEditor() {
    setEditingId(null);
    setAutoConsult(null);
  }

  async function confirmIds(ids: string[]) {
    if (ids.length === 0) return;
    setConfirming(true);
    onConfirming?.(true);
    try {
      const updated: Knowledge[] = [];
      for (const id of ids) {
        updated.push(await updateKnowledge(id, { status: "confirmed" }));
      }
      onUpdated(updated);
      onMessage({
        kind: "ok",
        text: `${updated.length}件を承認しました。AIに聞く画面から検索できます。`,
      });
    } catch (e) {
      onMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setConfirming(false);
      onConfirming?.(false);
    }
  }

  /** 編集の結果を返す。承認まで済んだものは呼び出し側で下書きから外れる */
  function applySaved(updated: Knowledge) {
    closeEditor();
    onUpdated([updated]);
    onMessage({
      kind: "ok",
      text:
        updated.status === "confirmed"
          ? `「${updated.title}」を保存して承認しました。`
          : `「${updated.title}」の変更を保存しました。承認するとAIが使えるようになります。`,
    });
  }

  return (
    <div
      data-pet-anchor={anchor}
      className="space-y-3 rounded-2xl bg-white p-4 ring-1 ring-slate-200/80"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-slate-800">{title}</h3>
        <button
          type="button"
          onClick={() => void confirmIds(drafts.map((k) => k.id))}
          disabled={confirming || editingId !== null}
          className="rounded-xl bg-indigo-600 px-3.5 py-1.5 text-xs font-medium text-white
                     transition-colors hover:bg-indigo-500 disabled:bg-slate-200 disabled:text-slate-400"
        >
          {confirming ? "承認しています…" : "すべて承認する"}
        </button>
      </div>
      <p className="text-xs text-slate-400">{hint}</p>

      {drafts.map((k, i) =>
        editingId === k.id ? (
          <KnowledgeEditor
            key={k.id}
            knowledge={k}
            sourceText={sourceText}
            offerConfirm
            autoConsult={autoConsult}
            onSaved={applySaved}
            onCancel={closeEditor}
            onAiBusy={onAiBusy}
          />
        ) : (
          // 1枚ずつ現れる。まとめて出ると、何件出たのかを数え直すことになる
          <div
            key={k.id}
            className="agent-rise overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"
            style={{ animationDelay: `${i * CARD_STAGGER_MS}ms` }}
          >
            <KnowledgeCard
              flush
              knowledge={k}
              showEmptyDetails
              extra={
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">
                  {k.status}
                </span>
              }
              actions={
                <>
                  <button
                    type="button"
                    onClick={() => askAi(k.id, "")}
                    disabled={confirming}
                    className="text-slate-500 underline underline-offset-2 hover:text-slate-700
                               disabled:text-slate-300"
                  >
                    手で直す
                  </button>
                  <button
                    type="button"
                    onClick={() => void confirmIds([k.id])}
                    disabled={confirming}
                    className="text-indigo-600 underline underline-offset-2 hover:text-indigo-500
                               disabled:text-slate-300"
                  >
                    承認する
                  </button>
                </>
              }
            />
            {/* AIに聞きたいのは「これでいいか分からない」段階であって、
                直すと決めた後ではない。ボタン1つで相談が始まる */}
            <div className="px-4 pb-3">
              <AiConsultBar onAsk={(text) => askAi(k.id, text)} disabled={confirming} />
            </div>
          </div>
        ),
      )}
    </div>
  );
}
