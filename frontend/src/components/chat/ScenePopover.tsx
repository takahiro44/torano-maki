/**
 * 「その話、どの場面？」にその場で答える札。
 *
 * **順位と類似度だけでは納得できない。** 上位に来た理由は本文にあり、
 * 音声から起こしたナレッジなら、元の会話のどこを指しているかまで辿れる
 * （`GET /knowledge/{id}/evidence`）。一覧から離れずに読めることが大事で、
 * 別画面へ飛ばすと「探している最中」の流れが切れる。
 *
 * **開いてから取りに行く。** 候補は20件以上あり、全部の会話を先読みすると
 * 回答のストリーミングと帯域を取り合う。
 *
 * **ここから直せる。** 登録済みの誤りに気づくのは、たいてい検索して読み返した
 * この瞬間で、直せる場所が別画面にあると次に開いたときには忘れている。
 * 使うのは登録画面と同じ編集フォーム（KnowledgeEditor）で、AIと相談しながら
 * 書き換えられる。
 */

import { useEffect, useState } from "react";
import { deleteKnowledge, getKnowledge, getKnowledgeEvidence } from "../../api/client";
import { knowledgeCategoryBadge } from "../../lib/knowledgeCategory";
import { navigate, roleplayStartPath } from "../../lib/router";
import type { Knowledge, KnowledgeEvidenceSpan } from "../../types/api";
import { KnowledgeArticle } from "../KnowledgeArticle";
import { AiConsultBar, KnowledgeEditor } from "../KnowledgeEditor";
import { Spinner } from "./AgentTimeline";

const SPEAKER_LABEL: Record<string, string> = {
  salesperson: "営業",
  customer: "顧客",
  source: "原文",
  unknown: "不明",
};

export type SceneTarget = {
  knowledgeId: string;
  title: string;
  semanticScore: number | null;
  cited: boolean;
  /** 押した行の位置。札はその高さに合わせて出す */
  top: number;
  left: number;
};

const CARD_WIDTH = 400;

/** 編集中だけ広げる。13項目の入力欄を400pxに詰めると読めない */
const EDIT_CARD_WIDTH = 560;

/** 画面の縁からこれだけは離す */
const EDGE_PAD = 12;

/**
 * 札の高さの下限。
 *
 * **押した行の高さに素直に合わせると、下端が画面の外に出る。** 札の下端には
 * 「この場面を練習する」と「手で編集する」があり、そこが消えると
 * 一覧から練習にも編集にも入れなくなる。
 */
const MIN_CARD_HEIGHT = 260;

function timeLabel(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function ScenePopover({
  target,
  onClose,
  onEdited,
  onDeleted,
}: {
  target: SceneTarget;
  onClose: () => void;
  /** 保存されたら呼ぶ。呼び出し側が一覧と件数を取り直すため */
  onEdited?: () => void;
  /** 削除されたら呼ぶ。呼び出し側が一覧と件数を取り直すため */
  onDeleted?: () => void;
}) {
  const [spans, setSpans] = useState<KnowledgeEvidenceSpan[] | null>(null);
  const [knowledge, setKnowledge] = useState<Knowledge | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState(false);
  // 開くと同時にAIへ投げる指示。「直す」を挟まずに相談へ入るため
  const [autoConsult, setAutoConsult] = useState<string | null>(null);

  function askAi(instruction: string) {
    setAutoConsult(instruction || null);
    setEditing(true);
  }

  function closeEditor() {
    setEditing(false);
    setAutoConsult(null);
  }

  async function handleDelete() {
    if (!window.confirm("このナレッジを削除しますか？")) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteKnowledge(target.knowledgeId);
      onDeleted?.();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }

  // 別の行を押したときは呼び出し側が key で作り直すので、ここで初期化しない
  useEffect(() => {
    let alive = true;
    Promise.all([getKnowledgeEvidence(target.knowledgeId), getKnowledge(target.knowledgeId)])
      .then(([evidence, record]) => {
        if (!alive) return;
        setSpans(evidence);
        setKnowledge(record);
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [target.knowledgeId]);

  // Escapeで閉じる。読み終わったあと、閉じるボタンを探させない。
  // **編集中は効かせない。** 書きかけがキー1つで消えるのは事故になる
  useEffect(() => {
    if (editing) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, editing]);

  const utterances = (spans ?? []).flatMap((span) => span.utterances);
  const width = editing ? EDIT_CARD_WIDTH : CARD_WIDTH;
  // 画面に収める。収まらないと下端の操作が押せない（MIN_CARD_HEIGHT）
  const top = Math.max(
    EDGE_PAD,
    Math.min(target.top, window.innerHeight - MIN_CARD_HEIGHT - EDGE_PAD),
  );
  const left = Math.max(EDGE_PAD, Math.min(target.left, window.innerWidth - width - EDGE_PAD));
  // 根拠の発話をつないだものが、このナレッジのもとの原文。
  // AIに直させるとき、これが無いと今の値だけで書き直すことになる
  const sourceText = utterances.map((u) => u.content).join("\n") || null;

  return (
    <>
      {/* 外側を押したら閉じる。札の外に注意が移った時点で用は済んでいる。
          編集中だけは閉じない（書きかけを誤って捨てさせないため） */}
      <div
        className="fixed inset-0 z-30"
        onClick={editing ? undefined : onClose}
        aria-hidden="true"
      />

      <aside
        data-pet-anchor="popup"
        className="agent-rise fixed z-40 flex flex-col overflow-hidden rounded-xl bg-white shadow-xl ring-1 ring-slate-200"
        style={{
          top,
          left,
          width,
          maxWidth: `calc(100vw - ${EDGE_PAD * 2}px)`,
          // 高さは出した位置から決める。固定の 80vh だと、下の方の行を
          // 押したときだけ下端がはみ出す
          maxHeight: `calc(100vh - ${top + EDGE_PAD}px)`,
        }}
      >
        <header className="flex items-start gap-2 border-b border-slate-100 px-3.5 py-2.5">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <p className="min-w-0 flex-1 truncate text-[13px] font-medium text-slate-800">
                {target.title}
              </p>
              {knowledge && (
                <span
                  className={
                    "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium " +
                    knowledgeCategoryBadge(knowledge.knowledge_type).className
                  }
                >
                  {knowledgeCategoryBadge(knowledge.knowledge_type).label}
                </span>
              )}
            </div>
            <p className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-400">
              {target.semanticScore !== null && (
                <span className="font-mono">意味の近さ {target.semanticScore.toFixed(3)}</span>
              )}
              {target.cited && <span className="text-indigo-600">AIが参照</span>}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="閉じる"
            className="rounded px-1.5 text-slate-300 hover:bg-slate-100 hover:text-slate-600"
          >
            ✕
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3.5 py-3">
          {error && <p className="text-xs text-rose-600">{error}</p>}

          {spans === null && !error && (
            <p className="flex items-center gap-2 text-xs text-slate-500">
              <Spinner />
              会話を読み込んでいます…
            </p>
          )}

          {knowledge && !editing && (
            <section>
              <h4 className="mb-1.5 text-[10px] font-medium text-slate-400">構造化データ</h4>
              <KnowledgeArticle knowledge={knowledge} showEmpty />
            </section>
          )}

          {knowledge && editing && (
            <KnowledgeEditor
              knowledge={knowledge}
              sourceText={sourceText}
              autoConsult={autoConsult}
              onSaved={(updated) => {
                setKnowledge(updated);
                closeEditor();
                onEdited?.();
              }}
              onCancel={closeEditor}
            />
          )}

          {spans !== null && utterances.length === 0 && !editing && (
            <p className="rounded-lg bg-slate-50 px-3 py-2.5 text-xs leading-relaxed text-slate-500">
              このナレッジには会話の記録がありません。音声から取り込んだものだけ、
              元の商談のどこを指しているかを辿れます。
            </p>
          )}

          {/* AIに聞きたいのは「これでいいか分からない」段階であって、直すと
              決めた後ではない。読んだ直後の位置に置き、ボタン1つで相談が始まる */}
          {knowledge && !editing && <AiConsultBar onAsk={askAi} />}

          {utterances.length > 0 && !editing && (
            <section>
              <h4 className="text-[10px] font-medium text-slate-400">
                この記事のもとになった会話
                <span className="ml-1.5">{utterances.length}発言</span>
              </h4>
              <ol className="mt-1.5 space-y-2 border-l-2 border-indigo-200 pl-3">
                {utterances.map((u) => (
                  <li key={u.id} className="text-xs leading-relaxed">
                    <span
                      className={
                        "mr-1.5 font-medium " +
                        (u.speaker === "customer" ? "text-indigo-600" : "text-slate-400")
                      }
                    >
                      {SPEAKER_LABEL[u.speaker] ?? u.speaker}
                      {u.end_sec > 0.05 && (
                        <span className="ml-1 font-mono text-[10px] text-slate-300">
                          {timeLabel(u.start_sec)}
                        </span>
                      )}
                    </span>
                    <span className="text-slate-700">{u.content}</span>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>

        {/* 編集中は自前の保存・やめるを持っているので、こちらの導線は引っ込める。
            2種類の「やめる」が並ぶと、どちらが書きかけを捨てるのか分からない。
            **フッターは常に2つのボタンだけに保つ。** ここに他のものを足すと
            札が縦に伸び、下の方の行から開いたときに画面外へ出る */}
        {!editing && (
          <footer className="flex shrink-0 items-center gap-2 border-t border-slate-100 px-3.5 py-2.5">
            <button
              type="button"
              onClick={() => void handleDelete()}
              disabled={deleting}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-rose-600
                         hover:bg-rose-50 disabled:text-slate-300"
            >
              {deleting ? "削除中…" : "削除"}
            </button>
            <button
              type="button"
              onClick={() => navigate(roleplayStartPath({ knowledgeId: target.knowledgeId }))}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
            >
              この場面を練習する
            </button>
            <button
              type="button"
              onClick={() => askAi("")}
              disabled={!knowledge || deleting}
              className="ml-auto rounded-lg px-3 py-1.5 text-xs font-medium text-indigo-600
                         ring-1 ring-indigo-200 hover:bg-indigo-50
                         disabled:text-slate-300 disabled:ring-slate-200"
            >
              手で編集する
            </button>
          </footer>
        )}
      </aside>
    </>
  );
}
