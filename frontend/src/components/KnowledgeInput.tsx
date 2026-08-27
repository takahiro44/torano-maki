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
 * **抽出結果の確認・修正・承認は共有部品に置いた**（KnowledgeDrafts）。
 * 上司レビューでも同じ作業をするため、ここに抱えると片方だけ直る。
 *
 * **待っている間、この子が居る。** 抽出は1分近くかかる。止まったのか動いて
 * いるのかを読み取る負担を減らすのは、調査ビューと同じ理由（AgentPet）。
 *
 * **出し切ったら祝う。** ナレッジを貯める作業は、出した本人には見返りが無い。
 * 役に立つのはずっと後の、見えないところでのこと。せめて終わった瞬間だけは
 * 形にしておく（Celebration）。
 *
 * **会話から開かれたときは、きっかけの質問を出す。** AIが答えられなかった
 * 直後にここへ来ることが多く、そのとき書くべきなのは「さっき聞いたあの件」
 * である。何を書けばいいか思い出すところから始めさせない。
 */

import { useState } from "react";
import { ingestText } from "../api/client";
import { formatClock } from "../lib/time";
import type { AudioTranscribeResponse, Knowledge } from "../types/api";
import { Celebration } from "./Celebration";
import { AgentPet } from "./chat/AgentPet";
import { Spinner } from "./chat/AgentTimeline";
import type { Phase } from "./chat/phase";
import { ExtractionProgress } from "./ExtractionProgress";
import { KnowledgeDrafts } from "./KnowledgeDrafts";
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
  // 承認そのものは KnowledgeDrafts が行う。ここが持つのは
  // 「その間は入力を止める」ためと、アシスタントの機嫌のため
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState<Knowledge[]>([]);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  // 送った原文。抽出中の表示に映し、抽出後はAI相談の裏取りに渡す。
  // 本文欄は成功時に空にするので、そちらは使えない
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  // この抽出で出た件数。祝いに出す数はこれ（承認するたびに pending から
  // 抜けるので、承認し終えた時点では数え直せない）
  const [batchSize, setBatchSize] = useState(0);
  // 祝いに出す件数。出していないときは null（通し番号は cheer が兼ねる）
  const [celebration, setCelebration] = useState<number | null>(null);
  // ペットへの合図。増えるたびに数秒だけ喜ぶ
  const [cheer, setCheer] = useState(0);

  /**
   * 下書きを出し切ったときだけ祝う。
   *
   * **1件ごとには祝わない。** まだ残っているのに終わったように見え、
   * 3件登録すれば3回出ることになって、すぐ煩わしくなる。
   */
  function celebrateIfDone(remaining: number) {
    if (remaining > 0 || batchSize === 0) return;
    setCelebration(batchSize);
    setCheer((n) => n + 1);
  }

  /** ファイルから来た本文を受け取る。打ちかけの内容は消さず、続きに足す */
  function accept(source: PickedSource) {
    setContent((prev) => (prev.trim() ? `${prev.trim()}\n\n${source.text}` : source.text));
    setTranscript(source.transcript);
    setSourceName(source.fileName);
    setShowSegments(false);
    setPending([]);
    setBatchSize(0);
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
    setSubmitted(text);
    setMessage(null);
    setCelebration(null);
    try {
      const result = await ingestText(text, transcript?.data_source_id);
      if (result.saved.length === 0) {
        setPending([]);
        setBatchSize(0);
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
      setBatchSize(result.saved.length);
      const extra = result.notes?.length ? ` ${result.notes.join(" ")}` : "";
      setMessage({
        kind: "ok",
        text: `${result.saved.length}件を構造化しました。内容を確認し、必要なら直してから承認すると、AIに聞く画面の検索・一覧の対象になります。${extra}`,
      });
      onCreated();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  }

  /**
   * 承認・保存された分を手元へ返す。
   *
   * **まとめて受け取る。** 「すべて承認する」で1件ずつ返されると、
   * 最後の1件を処理する時点でも残り件数を数え直せず、出し切った瞬間に
   * 祝えない（celebrateIfDone）
   */
  function applyUpdated(updated: Knowledge[]) {
    const byId = new Map(updated.map((k) => [k.id, k]));
    const next = pending.map((k) => byId.get(k.id) ?? k);
    setPending(next);
    if (updated.some((k) => k.status === "confirmed")) {
      celebrateIfDone(next.filter((k) => k.status === "draft").length);
    }
    onCreated();
  }

  const drafts = pending.filter((k) => k.status === "draft");
  const busy = saving || confirming;

  return (
    <div className="space-y-5">
      <AgentPet
        scene="ingest"
        phase={petPhase({ saving, confirming, aiBusy, drafts: drafts.length, message })}
        foundCount={drafts.length}
        cheer={cheer}
      />

      {/* 祝いは全面に出るが操作は素通しする（Celebration）。
          key を入れ替えて、続けて登録したときに最初から出し直す */}
      {celebration !== null && (
        <Celebration key={cheer} count={celebration} onDone={() => setCelebration(null)} />
      )}

      <div>
        <h2 className="text-xl font-semibold tracking-tight text-slate-900">ナレッジを登録</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-slate-500">
          形式は問いません。走り書きでも、商談の録音でも、議事録ファイルでも構いません。
          AIが構造化し、この画面で確認・修正して承認すると検索対象になります。
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

      <div
        data-pet-anchor="ingest-composer"
        className="rounded-2xl bg-white p-2 shadow-sm ring-1 ring-slate-200 focus-within:ring-2 focus-within:ring-indigo-400"
      >
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

      {saving && submitted && <ExtractionProgress text={submitted} />}

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
          data-pet-anchor="ingest-message"
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

      <KnowledgeDrafts
        drafts={drafts}
        sourceText={submitted}
        anchor="ingest-result"
        onUpdated={applyUpdated}
        onMessage={setMessage}
        onAiBusy={setAiBusy}
        onConfirming={setConfirming}
      />

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

/**
 * この画面の「今の段階」をアシスタントの気分に翻訳する。
 *
 * **フェーズの判定を1箇所に置く。** 吹き出しと表情と居場所がそれぞれ
 * 独自に判定すると、同じ瞬間に別のことを言い出す（chat/phase.ts と同じ理由）。
 *
 * 失敗を成果より先に見る。抽出に失敗した直後に前回の結果が残っていると、
 * 「できたよ！」と言いながらエラーが出ている状態になる。
 */
function petPhase(state: {
  saving: boolean;
  confirming: boolean;
  aiBusy: boolean;
  drafts: number;
  message: { kind: "ok" | "error" } | null;
}): Phase {
  if (state.saving || state.aiBusy) return "searching";
  if (state.confirming) return "answering";
  if (state.message?.kind === "error") return "error";
  return state.drafts > 0 ? "done" : "idle";
}
