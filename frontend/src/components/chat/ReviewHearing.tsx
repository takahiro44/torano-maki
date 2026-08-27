/**
 * 上司へ送る前の聞き取り。
 *
 * **要約だけでは、上司に渡すものが半分しかない。** 会話ログから読み取れるのは
 * 「何を聞いて、何が出てこなかったか」までで、**本人が自分でどこを怪しいと
 * 思っているか**は会話に現れない。AIの回答をそのまま繰り返しただけの箇所も、
 * ログの上では理解できていたように見える。そこを聞くのがこの画面である。
 *
 * **2問で終わらせる。** 論点ごと・疑問ごとに1画面ずつ聞く形にしたら、
 * 送るまでに7回も8回も押すことになった。聞きたいのは「あやしいのはどれか」と
 * 「ほかに何が聞きたいか」の2つだけで、それ以外は既定のまま送れてよい。
 * 手数が増えるほど、答えではなく「早く終わらせる押し方」になる。
 *
 * **AIに質問文を書かせない。** 問いは診断の中身から機械的に決まる
 * （actions.ts と同じ理由）。ここでLLMを呼ぶと、待たせている最中にさらに
 * 数十秒足すことになり、しかも何を聞くか保証できない。
 *
 * **答えはサーバで作り直させない。** 本人が読んだ文面と自己申告を
 * そのまま送る（api/client.ts の sendChatReview）。
 */

import { useState } from "react";
import type {
  ChatReviewDiagnosis,
  HearingQuestion,
  ReviewHearing as Hearing,
} from "../../types/api";
import { PetFace } from "./AgentPet";

/** 名乗りは毎回打たせない。認証が無いぶん、ここだけは端末に覚えさせる */
const NAME_KEY = "torano-maki:learner-name";

function loadName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

function rememberName(value: string): void {
  try {
    if (value) localStorage.setItem(NAME_KEY, value);
    else localStorage.removeItem(NAME_KEY);
  } catch {
    // 覚えられなくても、その場の送信には効く
  }
}

type Props = {
  diagnosis: ChatReviewDiagnosis;
  sending: boolean;
  onSend: (hearing: Hearing) => void;
  onCancel: () => void;
};

export function ReviewHearing({ diagnosis, sending, onSend, onCancel }: Props) {
  // 理解できていた事項が無ければ1問目は成立しない。いきなり2問目から始める
  const asksLevel = diagnosis.understood_points.length > 0;
  const [step, setStep] = useState(asksLevel ? 0 : 1);
  // **「あやしい」に入れたものだけを持つ。** 3段階から選ばせるのをやめ、
  // 押したものが怪しい、というトグル1つにした。既定（押さない）は
  // 要約がそう読んだとおりの「説明できる」になる
  const [shaky, setShaky] = useState<Set<number>>(new Set());
  // 既定は「聞く」。AIが「埋まらなかった」と判断した疑問なので、
  // 外すのは本人が「もう解決した」と思ったときだけでよい
  const [dropped, setDropped] = useState<Set<number>>(new Set());
  const [own, setOwn] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [name, setName] = useState(loadName);

  function toggle(set: Set<number>, index: number): Set<number> {
    const next = new Set(set);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    return next;
  }

  function addOwn() {
    const text = draft.trim();
    if (!text) return;
    setOwn((prev) => [...prev, text]);
    setDraft("");
  }

  function send() {
    const trimmed = name.trim();
    rememberName(trimmed);

    // 書きかけを捨てない。「足す」を押し忘れただけで質問が消えるのは事故
    const written = draft.trim() ? [...own, draft.trim()] : own;
    // **本人が書いた問いを先に置く。** 上司が最初に読むのは会話に現れなかった方。
    // AIが拾った疑問はログを読めば分かるが、こちらは本人しか知らない
    const questions: HearingQuestion[] = [
      ...written.map((question) => ({ question, source: "learner" as const })),
      ...diagnosis.gaps
        .filter((_, i) => !dropped.has(i))
        .map((g) => ({ question: g.gap, source: "agent" as const })),
    ];
    onSend({
      learner_name: trimmed || null,
      summary: diagnosis.summary,
      understood: diagnosis.understood_points.map((point, i) => ({
        point,
        level: shaky.has(i) ? "shaky" : "understood",
      })),
      questions,
    });
  }

  const askCount = diagnosis.gaps.length - dropped.size + own.length + (draft.trim() ? 1 : 0);

  return (
    <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200/80">
      <div className="flex items-start gap-2">
        {/* 聞いているのが誰かを顔で出す。フォームに見えると、答えではなく
            入力欄を埋める作業になる */}
        <span className="mt-0.5 shrink-0">
          <PetFace scene="chat" mood={step === 0 ? "thinking" : "done"} sizeClass="size-8" />
        </span>

        <div className="min-w-0 flex-1">
          {step === 0 ? (
            <>
              <p className="text-sm font-medium text-slate-800">この中で、まだあやしいのはどれ？</p>
              <p className="mt-0.5 text-[10.5px] text-slate-400">
                押したものは「本人は説明できないそうです」と上司に伝える。無ければそのまま次へ
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {diagnosis.understood_points.map((point, i) => (
                  <button
                    key={i}
                    onClick={() => setShaky((prev) => toggle(prev, i))}
                    className={
                      "rounded-lg px-2.5 py-1.5 text-left text-xs ring-1 transition-colors " +
                      (shaky.has(i)
                        ? "bg-amber-50 text-amber-900 ring-amber-200"
                        : "bg-white text-slate-700 ring-slate-200/80 hover:bg-slate-50")
                    }
                  >
                    {point}
                    {shaky.has(i) && (
                      <span className="ml-1 text-[10.5px] text-amber-700">あやしい</span>
                    )}
                  </button>
                ))}
              </div>
              <button
                onClick={() => setStep(1)}
                className="mt-2 rounded-lg bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700
                           ring-1 ring-slate-200/80 hover:bg-slate-50"
              >
                次へ
              </button>
            </>
          ) : (
            <>
              <p className="text-sm font-medium text-slate-800">上司に聞きたいことは？</p>

              {diagnosis.gaps.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {diagnosis.gaps.map((gap, i) => (
                    <button
                      key={i}
                      onClick={() => setDropped((prev) => toggle(prev, i))}
                      title={dropped.has(i) ? "押すと質問に戻す" : "押すと質問から外す"}
                      className={
                        "rounded-lg px-2.5 py-1.5 text-left text-xs ring-1 transition-colors " +
                        (dropped.has(i)
                          ? "bg-white text-slate-400 line-through ring-slate-200/80"
                          : "bg-amber-50 text-amber-900 ring-amber-200 hover:bg-amber-100")
                      }
                    >
                      {gap.gap}
                      {gap.db_state === "missing" && !dropped.has(i) && (
                        <span className="ml-1 text-[10.5px] text-amber-700/70">蓄積に無い</span>
                      )}
                    </button>
                  ))}
                </div>
              )}

              {own.length > 0 && (
                <ul className="mt-1.5 flex flex-wrap gap-1.5">
                  {own.map((q, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-1.5 rounded-lg bg-indigo-50 px-2.5 py-1.5 text-xs text-indigo-900"
                    >
                      <span className="min-w-0">{q}</span>
                      <button
                        onClick={() => setOwn((prev) => prev.filter((_, j) => j !== i))}
                        className="shrink-0 text-indigo-400 hover:text-indigo-700"
                        aria-label="この質問を消す"
                      >
                        ✕
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                // 変換確定のEnterで送ってしまうと書きかけが飛ぶ。
                // 追加は Ctrl/Cmd+Enter に分ける（RoleplaySession と同じ）
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) {
                    addOwn();
                  }
                }}
                rows={2}
                placeholder="ほかに分からなかったこと（例：在庫差異、どこまで自分で判断していい？）"
                className="mt-1.5 w-full resize-y rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs
                           outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
              />

              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={40}
                  placeholder="名前（任意）"
                  className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-xs
                             outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
                />
                <button
                  onClick={send}
                  disabled={sending || askCount === 0}
                  className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white
                             hover:bg-indigo-500 disabled:opacity-50"
                >
                  {sending ? "送っています…" : `上司に質問する（${askCount}件）`}
                </button>
                <button
                  onClick={onCancel}
                  disabled={sending}
                  className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-500
                             hover:bg-slate-100 disabled:opacity-50"
                >
                  送らない
                </button>
                {asksLevel && (
                  <button
                    onClick={() => setStep(0)}
                    disabled={sending}
                    className="text-[10.5px] text-slate-400 underline hover:text-slate-600 disabled:opacity-50"
                  >
                    ひとつ戻る
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
