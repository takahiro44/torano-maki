/**
 * ナレッジを登録する画面。**テキスト・音声・ファイルを1つの入口にまとめる。**
 *
 * **入口を分けない。** 以前は「登録」と「音声」が別のタブで、同じ
 * 「ナレッジを貯める」作業なのに行き先が2つあった。抽出の入力は結局
 * テキストなので、経路を分ける理由は無い（旧 AudioIngest の判断）。
 * 何で持っているか（メモ・録音・議事録ファイル）で人に選ばせない。
 *
 * **文字起こしは本文欄に入るだけ。** 聞き取りの誤りは後段のLLMでは直せず、
 * 誤りに見えないまま自然な文で埋められる。必ず人が目を通す段を挟む。
 *
 * 分類フォームは出さない。入力の手間を増やさないことが価値のため。
 * 承認は一覧へ行かず、この画面で完了できるようにする。
 *
 * **会話から開かれたときは、きっかけの質問を出す。** AIが答えられなかった
 * 直後にここへ来ることが多く、そのとき書くべきなのは「さっき聞いたあの件」
 * である。何を書けばいいか思い出すところから始めさせない。
 */

import { useState } from "react";
import { ingestText, updateKnowledge } from "../api/client";
import { formatClock } from "../lib/time";
import type { AudioTranscribeResponse, Knowledge } from "../types/api";
import { Spinner } from "./chat/AgentTimeline";
import { KnowledgeCard } from "./KnowledgeCard";
import { MicButton } from "./MicButton";
import { SourcePicker, type PickedSource } from "./SourcePicker";

const EXAMPLES = [
  "先日、田中製作所様との商談で、他社より価格が高いと指摘されました。すぐに値引きせず、「他社の製品とどの点を比較されていますか？」と聞きました。保守対応の質を重視していると分かったので、24時間対応と現地エンジニア常駐を説明したところ、価格差を理解いただき受注できました。",
  "B商事の担当が来月から交代。前任との関係を新任に引き継がないと、更新の話が止まる。",
  "A社の初回訪問で、標準プラン360万円が年間予算300万円を超えると言われた。その場で値引きせず、営業部30名だけの段階導入を出した。「稟議しやすい」と言われ、次回は情シス入りの見積になった。",
];

type Props = {
  onCreated: () => void;
  /** 会話から渡ってきたきっかけ（質問文）。手で開いたときは null */
  note?: string | null;
};

export function KnowledgeInput({ onCreated, note = null }: Props) {
  const [content, setContent] = useState("");
  // 音声から来た本文は出典を持つ。抽出時に渡すと、ナレッジが元の商談に紐づく
  const [transcript, setTranscript] = useState<AudioTranscribeResponse | null>(null);
  const [sourceName, setSourceName] = useState<string | null>(null);
  const [showSegments, setShowSegments] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState<Knowledge[]>([]);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  /** ファイルから来た本文を受け取る。打ちかけの内容は消さず、続きに足す */
  function accept(source: PickedSource) {
    setContent((prev) => (prev.trim() ? `${prev.trim()}\n\n${source.text}` : source.text));
    setTranscript(source.transcript);
    setSourceName(source.fileName);
    setShowSegments(false);
    setPending([]);
    setMessage({
      kind: "ok",
      text: source.transcript
        ? `${source.fileName} を文字起こししました（${formatClock(source.transcript.duration_sec)} / ${source.transcript.segments.length}区間 / ${source.text.length}文字）。誤りを直してから登録してください。`
        : `${source.fileName} を読み込みました（${source.text.length}文字）。`,
    });
  }

  /** マイクの結果も本文へ足すだけ。送信は人が決める */
  function appendSpoken(text: string) {
    if (!text.trim()) return;
    setContent((prev) => (prev.trim() ? `${prev.trim()}\n${text}` : text));
  }

  async function submit() {
    const text = content.trim();
    if (!text) return;
    setSaving(true);
    setMessage(null);
    try {
      const result = await ingestText(text, transcript?.data_source_id);
      if (result.saved.length === 0) {
        setPending([]);
        setMessage({
          kind: "error",
          text: "具体的なナレッジを抽出できませんでした。エピソードをもう少し詳しく書いてみてください。",
        });
        return;
      }
      setContent("");
      setTranscript(null);
      setSourceName(null);
      setPending(result.saved);
      const extra = result.notes?.length ? ` ${result.notes.join(" ")}` : "";
      setMessage({
        kind: "ok",
        text: `${result.saved.length}件を構造化しました。内容を確認して承認すると、AIに聞く画面の検索・一覧の対象になります。${extra}`,
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
        text: `${updated.length}件を承認しました。AIに聞く画面から検索できます。`,
      });
      onCreated();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setConfirming(false);
    }
  }

  const drafts = pending.filter((k) => k.status === "draft");
  const busy = saving || confirming;

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold tracking-tight text-slate-900">ナレッジを登録</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-slate-500">
          形式は問いません。走り書きでも、商談の録音でも、議事録ファイルでも構いません。
          AIが構造化し、この画面で確認して承認すると検索対象になります。
        </p>
      </div>

      {note && (
        <div className="rounded-2xl bg-amber-50 px-4 py-3 ring-1 ring-amber-200/70">
          <p className="text-xs font-medium text-amber-800">
            この質問に答えられるナレッジがありませんでした
          </p>
          <p className="mt-1 text-sm leading-relaxed text-amber-900/80">{note}</p>
        </div>
      )}

      <div className="rounded-2xl bg-white p-2 shadow-sm ring-1 ring-slate-200 focus-within:ring-2 focus-within:ring-indigo-400">
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void submit();
          }}
          rows={10}
          placeholder="例）先日の商談で価格が高いと言われたので、いきなり値引きせず…"
          aria-label="ナレッジの元になる文章"
          className="w-full resize-y bg-transparent px-2.5 py-2 text-[15px] leading-7
                     text-slate-900 outline-none placeholder:text-slate-400"
        />

        <div className="flex items-center justify-between gap-2 px-1 pt-1">
          <div className="flex min-w-0 items-center gap-2">
            <MicButton onTranscribed={appendSpoken} disabled={busy} />
            <span className="truncate text-[11px] text-slate-400">
              {content.length > 0 ? `${content.length}文字` : "話して入力できます"}
              {sourceName && ` ・ ${sourceName}`}
            </span>
          </div>

          <button
            onClick={() => void submit()}
            disabled={busy || !content.trim()}
            className="flex shrink-0 items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2
                       text-sm font-medium text-white transition-colors hover:bg-indigo-500
                       disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
          >
            {saving && <Spinner className="size-4 text-white" />}
            {saving ? "抽出しています…" : "抽出して登録する"}
          </button>
        </div>
      </div>

      {/* 時刻つきの文字起こしは閲覧専用。本文を直すと時刻とずれるため
          （時刻はDBのセグメントに紐づいており、本文を直しても変わらない） */}
      {transcript && (
        <div className="rounded-2xl bg-white p-4 ring-1 ring-slate-200/80">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-medium text-slate-800">
              文字起こしの元データ
              <span className="ml-1.5 text-xs text-slate-400">
                {transcript.segments.length}区間
              </span>
            </h3>
            <button
              type="button"
              onClick={() => setShowSegments((v) => !v)}
              className="rounded-lg px-2 py-1 text-xs text-indigo-600 hover:bg-indigo-50"
            >
              {showSegments ? "閉じる" : "時刻つきで見る"}
            </button>
          </div>
          {showSegments && (
            <ul className="mt-3 max-h-96 space-y-1 overflow-y-auto rounded-xl bg-slate-50 p-3">
              {transcript.segments.map((s) => (
                <li key={s.sequence_no} className="flex gap-3 text-sm leading-relaxed">
                  <span className="shrink-0 font-mono text-[11px] text-slate-400">
                    {formatClock(s.start_sec)}
                  </span>
                  <span className="text-slate-700">{s.text}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <SourcePicker onPicked={accept} disabled={busy} />

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

      {drafts.length > 0 && (
        <div className="space-y-3 rounded-2xl bg-white p-4 ring-1 ring-slate-200/80">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-medium text-slate-800">抽出結果（下書き）</h3>
            <button
              type="button"
              onClick={() => void confirmIds(drafts.map((k) => k.id))}
              disabled={confirming}
              className="rounded-xl bg-indigo-600 px-3.5 py-1.5 text-xs font-medium text-white
                         transition-colors hover:bg-indigo-500 disabled:bg-slate-200 disabled:text-slate-400"
            >
              {confirming ? "承認しています…" : "すべて承認する"}
            </button>
          </div>
          <p className="text-xs text-slate-400">
            詳細を開くと根拠の原文も確認できます。承認しない場合は下書きのまま残ります。
          </p>
          {drafts.map((k) => (
            <KnowledgeCard
              key={k.id}
              knowledge={k}
              showEmptyDetails
              extra={
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">
                  {k.status}
                </span>
              }
              actions={
                <button
                  type="button"
                  onClick={() => void confirmIds([k.id])}
                  disabled={confirming}
                  className="text-indigo-600 underline underline-offset-2 hover:text-indigo-500"
                >
                  承認する
                </button>
              }
            />
          ))}
        </div>
      )}

      <div className="rounded-2xl bg-white p-4 ring-1 ring-slate-200/80">
        <p className="text-xs font-medium text-slate-500">
          デモ用の原文（クリックで入力欄に入ります）
        </p>
        <ul className="mt-2 space-y-1">
          {EXAMPLES.map((ex, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => setContent(ex)}
                className="w-full rounded-xl px-2.5 py-2 text-left text-sm leading-relaxed
                           text-slate-600 transition-colors hover:bg-indigo-50/60 hover:text-slate-900"
              >
                <span className="mr-2 text-[11px] font-medium text-slate-400">例{i + 1}</span>
                {ex.length > 80 ? `${ex.slice(0, 80)}…` : ex}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
