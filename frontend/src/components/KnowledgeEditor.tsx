/**
 * ナレッジを人が直す画面。**登録直後の下書きと、登録済みの一覧で同じものを使う。**
 *
 * **承認の直前に直せるようにする。** 抽出は当たっているのに一言だけ違う、
 * という状態が一番多い。そこで「承認する」しか出せないと、直したい人は
 * 承認してから別の場所で直すか、捨ててもう一度書くしかない。
 *
 * **AIに直接上書きさせない。** 相談の結果は提案として出すだけで、
 * 反映するかどうかは人が押して決める。直前の値がどこにも残らないまま
 * 書き換わると、何を直されたのか分からず、AIを信用する手がかりが消える。
 *
 * **変わった項目を明示する。** どこが変わったかはサーバが値を突き合わせて
 * 返す（`changed_fields`）。AIの自己申告だと、直していない項目を直したと
 * 言ったりその逆が起きる。
 *
 * **相談には編集中の値を送る。** 保存済みの値を送ると、人が手で直した内容を
 * AIが知らないまま書き直し、直したはずの箇所が巻き戻る。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { refineKnowledge, updateKnowledge } from "../api/client";
import type {
  Knowledge,
  KnowledgeDraftField,
  KnowledgeDraftFields,
  KnowledgeRefineResponse,
  RefineMessage,
} from "../types/api";
import { CBR_FIELD_LABELS } from "../types/api";
import { Spinner } from "./chat/AgentTimeline";

/** 1行で足りる項目。見出しと分類は長文にならない */
const SINGLE_LINE: ReadonlySet<KnowledgeDraftField> = new Set([
  "title",
  "industry",
  "product",
  "sales_stage",
]);

const FIELDS = CBR_FIELD_LABELS;

const LABEL_OF = new Map(FIELDS.map((f) => [f.key, f.label]));

/**
 * よくある頼み方。
 *
 * **毎回ゼロから書かせない。** 直したいことは分かっていても言語化に手間が
 * かかり、そこで面倒になって承認だけして終わる。
 *
 * **カードの上にも同じものを出す。** 相談に辿り着くのに「直す」を挟ませると、
 * 直すと決めた人しかAIに聞かない。聞きたいのは「これでいいか分からない」
 * 段階であって、直すと決めた後ではない（AiConsultBar）。
 */
const REFINE_PRESETS = [
  "固有名詞と数値を原文から拾い直して",
  "一般論になっている項目を、実際にやったことに書き直して",
  "学びを「次に同じ場面が来たら何をするか」の形にして",
  "適用場面と制約を埋めて",
  "全体を短くして",
];

const PLACEHOLDER = "例）値引きの経緯が抜けているので、原文から補って";

type Props = {
  knowledge: Knowledge;
  /** もとの原文。AI相談の裏取りに使う。無ければ今の値だけで相談する */
  sourceText?: string | null;
  onSaved: (updated: Knowledge) => void;
  onCancel: () => void;
  /** 「保存して承認する」を出すか。承認前の下書きで使う */
  offerConfirm?: boolean;
  /** AIと相談している間だけ true。呼び出し側がアシスタントの機嫌に使う */
  onAiBusy?: (busy: boolean) => void;
  /**
   * 開いた直後にこの指示で相談を始める。
   *
   * カードのボタン1つでAIに見てもらえるようにするため。開いてから
   * もう一度押させると、押す回数は「直す」を挟んでいた頃と変わらない。
   */
  autoConsult?: string | null;
};

function toDraft(k: Knowledge): KnowledgeDraftFields {
  return {
    title: k.title,
    situation: k.situation,
    problem: k.problem,
    judgment: k.judgment,
    action: k.action,
    reasoning: k.reasoning,
    outcome: k.outcome,
    lesson: k.lesson,
    applicable_situations: k.applicable_situations,
    limitations: k.limitations,
    industry: k.industry,
    product: k.product,
    sales_stage: k.sales_stage,
  };
}

function normalized(value: string | null): string {
  return (value ?? "").trim();
}

function changedFields(
  before: KnowledgeDraftFields,
  after: KnowledgeDraftFields,
): KnowledgeDraftField[] {
  return FIELDS.map((f) => f.key).filter(
    (key) => normalized(after[key]) !== normalized(before[key]),
  );
}

/**
 * PATCH に載せる差分を作る。
 *
 * **null を送らない。** バックエンドは明示的な null を拒む
 * （変更しない項目とクリアの区別が付かないため）。消したい項目は
 * 空文字で送る。
 */
type PatchBody = Partial<Record<KnowledgeDraftField, string>>;

function patchBody(before: KnowledgeDraftFields, after: KnowledgeDraftFields): PatchBody {
  const body: PatchBody = {};
  for (const key of changedFields(before, after)) {
    body[key] = after[key] ?? "";
  }
  return body;
}

export function KnowledgeEditor({
  knowledge,
  sourceText = null,
  onSaved,
  onCancel,
  offerConfirm = false,
  onAiBusy,
  autoConsult = null,
}: Props) {
  const original = useMemo(() => toDraft(knowledge), [knowledge]);
  const [draft, setDraft] = useState<KnowledgeDraftFields>(original);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [messages, setMessages] = useState<RefineMessage[]>([]);
  const [instruction, setInstruction] = useState("");
  const [refining, setRefining] = useState(false);
  const [proposal, setProposal] = useState<KnowledgeRefineResponse | null>(null);

  const dirty = changedFields(original, draft);
  const busy = saving || refining;

  function edit(key: KnowledgeDraftField, value: string) {
    // 空にしたら「未記入」に寄せる。空文字と null を両方持つと、
    // 未記入かどうかの判定が場所ごとに食い違う（KnowledgeDraft と同じ理由）
    const next = key === "title" ? value : value.trim() === "" ? null : value;
    setDraft((prev) => ({ ...prev, [key]: next }));
  }

  async function save(confirm: boolean) {
    if (!draft.title.trim()) {
      setError("タイトルは空にできません。");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const body = patchBody(original, draft);
      // 何も直していなくても承認は通す必要がある。空のPATCHは投げない
      const changes = confirm ? { ...body, status: "confirmed" as const } : body;
      if (Object.keys(changes).length === 0) {
        onCancel();
        return;
      }
      onSaved(await updateKnowledge(knowledge.id, changes));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function consult(text: string) {
    const asked = text.trim();
    if (!asked) return;
    setRefining(true);
    onAiBusy?.(true);
    setError(null);
    setProposal(null);
    // 履歴は送ってから積む。送る側に自分の発言が二重に入らないようにする
    const history = messages;
    setMessages([...history, { role: "user", content: asked }]);
    setInstruction("");
    try {
      const result = await refineKnowledge({ draft, instruction: asked, history, sourceText });
      setMessages((prev) => [...prev, { role: "assistant", content: result.comment }]);
      setProposal(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefining(false);
      onAiBusy?.(false);
    }
  }

  // **1回だけ投げる。** 開発時は StrictMode で effect が二度走るため、
  // 素直に書くと同じ相談でvLLMを2回叩き、片方の結果が捨てられる
  const autoFired = useRef(false);
  useEffect(() => {
    if (!autoConsult || autoFired.current) return;
    autoFired.current = true;
    void consult(autoConsult);
    // consult は draft を読むが、狙っているのは開いた時点の値。
    // 依存に足すと、入力のたびに投げ直す形になってしまう
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoConsult]);

  function applyProposal() {
    if (!proposal) return;
    setDraft(proposal.proposal);
    setProposal(null);
  }

  return (
    <div className="space-y-4 rounded-2xl bg-white p-4 ring-1 ring-indigo-200">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-slate-800">内容を直す</h3>
        <p className="text-[11px] text-slate-400">
          {dirty.length > 0 ? `${dirty.length}項目を変更中` : "まだ変更はありません"}
        </p>
      </div>

      <dl className="space-y-2.5">
        {FIELDS.map(({ key, label }) => {
          const changed = dirty.includes(key);
          return (
            <div key={key}>
              <dt className="mb-1 flex items-center gap-1.5">
                <label
                  htmlFor={`${knowledge.id}-${key}`}
                  className="text-xs font-medium text-slate-500"
                >
                  {label}
                </label>
                {changed && (
                  <span className="rounded bg-amber-100 px-1.5 py-px text-[10px] font-medium text-amber-700">
                    変更
                  </span>
                )}
              </dt>
              <dd>
                {SINGLE_LINE.has(key) ? (
                  <input
                    id={`${knowledge.id}-${key}`}
                    value={draft[key] ?? ""}
                    onChange={(e) => edit(key, e.target.value)}
                    disabled={busy}
                    placeholder="（未記入）"
                    className={
                      "w-full rounded-lg border px-2.5 py-1.5 text-sm leading-relaxed text-slate-800 " +
                      "outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 " +
                      "disabled:bg-slate-50 placeholder:text-slate-300 " +
                      (changed ? "border-amber-300 bg-amber-50/40" : "border-slate-200")
                    }
                  />
                ) : (
                  <textarea
                    id={`${knowledge.id}-${key}`}
                    value={draft[key] ?? ""}
                    onChange={(e) => edit(key, e.target.value)}
                    disabled={busy}
                    rows={2}
                    placeholder="（未記入）"
                    className={
                      "w-full resize-y rounded-lg border px-2.5 py-1.5 text-sm leading-relaxed text-slate-800 " +
                      "outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100 " +
                      "disabled:bg-slate-50 placeholder:text-slate-300 " +
                      (changed ? "border-amber-300 bg-amber-50/40" : "border-slate-200")
                    }
                  />
                )}
              </dd>
            </div>
          );
        })}
      </dl>

      <RefinePanel
        messages={messages}
        instruction={instruction}
        onInstruction={setInstruction}
        refining={refining}
        disabled={saving}
        proposal={proposal}
        draft={draft}
        onConsult={consult}
        onApply={applyProposal}
        onDismiss={() => setProposal(null)}
      />

      {error && (
        <p className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-sm text-rose-800 ring-1 ring-rose-200/70">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="rounded-xl px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-50"
        >
          やめる
        </button>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={() => void save(false)}
            disabled={busy || dirty.length === 0}
            className="rounded-xl px-3 py-1.5 text-xs font-medium text-indigo-600
                       ring-1 ring-indigo-200 transition-colors hover:bg-indigo-50
                       disabled:text-slate-300 disabled:ring-slate-200"
          >
            {saving ? "保存しています…" : "変更を保存"}
          </button>
          {offerConfirm && (
            <button
              type="button"
              onClick={() => void save(true)}
              disabled={busy}
              className="flex items-center gap-2 rounded-xl bg-indigo-600 px-3.5 py-1.5 text-xs
                         font-medium text-white transition-colors hover:bg-indigo-500
                         disabled:bg-slate-200 disabled:text-slate-400"
            >
              {saving && <Spinner className="size-3.5 text-white" />}
              保存して承認する
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * AIと相談する部分。
 *
 * **やりとりを残す。** 1往復で決まらないことの方が多く、
 * 「さっき何を頼んだか」が見えないと同じ指示を繰り返すことになる。
 */
function RefinePanel({
  messages,
  instruction,
  onInstruction,
  refining,
  disabled,
  proposal,
  draft,
  onConsult,
  onApply,
  onDismiss,
}: {
  messages: RefineMessage[];
  instruction: string;
  onInstruction: (v: string) => void;
  refining: boolean;
  disabled: boolean;
  proposal: KnowledgeRefineResponse | null;
  draft: KnowledgeDraftFields;
  onConsult: (text: string) => void;
  onApply: () => void;
  onDismiss: () => void;
}) {
  const busy = refining || disabled;

  /** 送信の条件はボタンと同じ。押す経路が2つあるので、片方だけ緩いと不整合になる */
  function submit() {
    if (busy || !instruction.trim()) return;
    onConsult(instruction);
  }

  return (
    <section className="space-y-2.5 rounded-2xl bg-indigo-50/50 p-3.5 ring-1 ring-indigo-100">
      <div className="flex items-baseline gap-2">
        <h4 className="text-xs font-medium text-indigo-900">AIと一緒に直す</h4>
        <p className="text-[11px] text-indigo-900/50">直し方を書くと、書き換え案が返ります</p>
      </div>

      {messages.length > 0 && (
        <ol className="space-y-1.5">
          {messages.map((m, i) => (
            <li
              key={i}
              className={
                "agent-rise max-w-[85%] rounded-xl px-3 py-1.5 text-xs leading-relaxed " +
                (m.role === "user"
                  ? "ml-auto bg-indigo-600 text-white"
                  : "bg-white text-slate-700 ring-1 ring-slate-200/80")
              }
            >
              {m.content}
            </li>
          ))}
        </ol>
      )}

      {refining && (
        <p className="flex items-center gap-2 text-xs text-indigo-900/70">
          <Spinner />
          AIが直し方を考えています…（1分ほどかかることがあります）
        </p>
      )}

      {proposal && (
        <ProposalDiff
          proposal={proposal}
          draft={draft}
          onApply={onApply}
          onDismiss={onDismiss}
          disabled={busy}
        />
      )}

      <div className="flex flex-wrap gap-1.5">
        {REFINE_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            onClick={() => onConsult(preset)}
            disabled={busy}
            className="rounded-full bg-white px-2.5 py-1 text-[11px] text-indigo-700
                       ring-1 ring-indigo-200 transition-colors hover:bg-indigo-100
                       disabled:text-slate-300 disabled:ring-slate-200"
          >
            {preset}
          </button>
        ))}
      </div>

      <div className="flex items-end gap-2">
        <textarea
          value={instruction}
          onChange={(e) => onInstruction(e.target.value)}
          onKeyDown={(e) => {
            // Enterの扱いはチャットの入力欄（Composer）と同じにする。
            // ここもAIとの会話であり、送り方だけ別の作法にする理由が無い
            if (e.key !== "Enter" || e.shiftKey) return;
            // 変換確定のEnterで送らない。keyCode 229 は古い実装への保険
            if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
            e.preventDefault();
            submit();
          }}
          disabled={busy}
          rows={2}
          placeholder={PLACEHOLDER}
          aria-label="AIへの指示"
          className="min-w-0 flex-1 resize-y rounded-xl border border-indigo-200 bg-white px-2.5 py-1.5
                     text-sm leading-relaxed text-slate-800 outline-none
                     focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100
                     disabled:bg-slate-50 placeholder:text-slate-300"
        />
        <button
          type="button"
          onClick={submit}
          disabled={busy || !instruction.trim()}
          className="shrink-0 rounded-xl bg-indigo-600 px-3.5 py-2 text-xs font-medium text-white
                     transition-colors hover:bg-indigo-500
                     disabled:bg-slate-200 disabled:text-slate-400"
        >
          相談する
        </button>
      </div>
    </section>
  );
}

/**
 * 提案の差分。
 *
 * **変更前を消さない。** 何が置き換わるのかを見ずに反映すると、
 * 気づかないうちに固有名詞や数値が落ちる。
 */
function ProposalDiff({
  proposal,
  draft,
  onApply,
  onDismiss,
  disabled,
}: {
  proposal: KnowledgeRefineResponse;
  draft: KnowledgeDraftFields;
  onApply: () => void;
  onDismiss: () => void;
  disabled: boolean;
}) {
  const changed = proposal.changed_fields;

  return (
    <div className="agent-rise space-y-2 rounded-xl bg-white p-3 ring-1 ring-indigo-200">
      {changed.length === 0 ? (
        <p className="text-xs leading-relaxed text-slate-500">
          AIは直すところが無いと判断しました。別の言い方で頼むか、手で直してください。
        </p>
      ) : (
        <>
          <p className="text-[11px] font-medium text-indigo-900">
            {changed.length}項目の書き換え案
          </p>
          <ul className="space-y-2">
            {changed.map((key) => (
              <li key={key}>
                <p className="text-[11px] font-medium text-slate-500">{LABEL_OF.get(key) ?? key}</p>
                <p className="mt-0.5 whitespace-pre-wrap text-xs leading-relaxed text-slate-400 line-through decoration-slate-300">
                  {draft[key]?.trim() || "（未記入）"}
                </p>
                <p className="mt-0.5 whitespace-pre-wrap text-xs leading-relaxed text-slate-800">
                  {proposal.proposal[key]?.trim() || "（未記入にする）"}
                </p>
              </li>
            ))}
          </ul>
          <div className="flex gap-2 pt-0.5">
            <button
              type="button"
              onClick={onApply}
              disabled={disabled}
              className="rounded-lg bg-indigo-600 px-3 py-1 text-[11px] font-medium text-white
                         hover:bg-indigo-500 disabled:bg-slate-200 disabled:text-slate-400"
            >
              反映する
            </button>
            <button
              type="button"
              onClick={onDismiss}
              disabled={disabled}
              className="rounded-lg px-3 py-1 text-[11px] text-slate-500 hover:bg-slate-100"
            >
              見送る
            </button>
          </div>
          <p className="text-[10px] text-slate-400">
            反映しても、まだ保存されません。下の「変更を保存」で確定します。
          </p>
        </>
      )}
    </div>
  );
}

/**
 * 抽出結果のカードに付く、AIと相談する口。
 *
 * **「直す」を挟まない。** AIに聞きたいのは「これでいいか分からない」段階で、
 * 直すと決めた後ではない。編集フォームの奥に置くと、直すと決めた人しか
 * AIに聞かないことになる。
 *
 * **選択肢と自由入力の両方を出す。** 直したいことは分かっていても言語化に
 * 手間がかかり、そこで面倒になって承認だけして終わる。かといって選択肢だけ
 * では、ここに書いてある以外のことが頼めない。
 *
 * 押すと編集フォームが開き、そのまま相談が始まる（`autoConsult`）。
 */
export function AiConsultBar({
  onAsk,
  disabled = false,
}: {
  onAsk: (instruction: string) => void;
  disabled?: boolean;
}) {
  const [text, setText] = useState("");

  function ask(instruction: string) {
    const asked = instruction.trim();
    if (disabled || !asked) return;
    setText("");
    onAsk(asked);
  }

  return (
    <section className="space-y-2 rounded-xl bg-indigo-50/60 p-3 ring-1 ring-indigo-100">
      <div className="flex items-baseline gap-2">
        <h4 className="text-xs font-medium text-indigo-900">AIと相談する</h4>
        <p className="text-[11px] text-indigo-900/50">選ぶか、直し方を書いてください</p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {REFINE_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            onClick={() => ask(preset)}
            disabled={disabled}
            className="rounded-full bg-white px-2.5 py-1 text-[11px] text-indigo-700
                       ring-1 ring-indigo-200 transition-colors hover:bg-indigo-100
                       disabled:text-slate-300 disabled:ring-slate-200"
          >
            {preset}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            // Enterの扱いはチャットの入力欄（Composer）と同じにする。
            // ここだけ違うと、同じAIへの入力なのに日本語の変換確定Enterで
            // 送られてしまう欄と、そうでない欄が混在する
            if (e.key !== "Enter" || e.shiftKey) return;
            // 変換確定のEnterで送らない。keyCode 229 は古い実装への保険
            if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
            e.preventDefault();
            ask(text);
          }}
          disabled={disabled}
          placeholder={PLACEHOLDER}
          aria-label="AIへの指示"
          className="min-w-0 flex-1 rounded-xl border border-indigo-200 bg-white px-2.5 py-1.5
                     text-sm leading-relaxed text-slate-800 outline-none
                     focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100
                     disabled:bg-slate-50 placeholder:text-slate-300"
        />
        <button
          type="button"
          onClick={() => ask(text)}
          disabled={disabled || !text.trim()}
          className="shrink-0 rounded-xl bg-indigo-600 px-3.5 py-1.5 text-xs font-medium text-white
                     transition-colors hover:bg-indigo-500
                     disabled:bg-slate-200 disabled:text-slate-400"
        >
          相談する
        </button>
      </div>
    </section>
  );
}
