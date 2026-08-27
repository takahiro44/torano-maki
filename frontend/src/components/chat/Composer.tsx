/**
 * 質問の入力欄。
 *
 * **Enterの扱いに `isComposing` の判定が要る。** 日本語入力では変換候補を
 * 確定するEnterでも keydown が発生する。判定しないと、変換を確定しただけで
 * 送信されてしまう。1回の回答に数十秒かかるこの画面では致命的だった。
 *
 * **1行の input ではなく textarea。** 商談の状況を書いて聞くことが多く、
 * 1行では書いた内容を確認できない。Shift+Enter で改行できるようにし、
 * 入力量に応じて高さを伸ばす。
 *
 * **マイクは入力欄を置き換えない。** 話した内容は入力欄に入るだけで、
 * 送信するかどうかは人が決める。STTは誤認識するため、確認の段を挟まないと
 * 誤った質問のまま Agent が検索し、数十秒待たされた末に的外れな回答が返る。
 */

import { useLayoutEffect, useRef, useState } from "react";
import { transcribeChatQuestion } from "../../api/client";
import { MicButton } from "../MicButton";
import { Spinner } from "./AgentTimeline";

/** これ以上は伸ばさない高さ。会話が見えなくなる方が困る */
const MAX_HEIGHT_PX = 200;

type Props = {
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
};

export function Composer({ busy, onSend, onStop }: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  // 内容に合わせて高さを変える。一度 auto に戻さないと縮まらない
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [text]);

  function submit() {
    if (busy || !text.trim()) return;
    onSend(text);
    setText("");
  }

  /**
   * 話した内容を入力欄へ足す。**送信はしない。**
   *
   * 置き換えではなく追記なのは、打ちかけの文に話して足す使い方があるため。
   * 追記後にカーソルを末尾へ置いてフォーカスを戻すのは、
   * そのまま直して Enter で送れるようにするため（マイクを押すと
   * フォーカスがボタンへ移り、戻さないと Enter がボタンを再度押す）。
   */
  function appendSpoken(spoken: string) {
    if (!spoken.trim()) return;
    const next = text.trim() ? `${text.trim()} ${spoken}` : spoken;
    setText(next);
    // 高さの再計算（useLayoutEffect）より後にカーソルを動かす
    requestAnimationFrame(() => {
      const el = ref.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(next.length, next.length);
    });
  }

  return (
    <div className="border-t border-slate-200/80 bg-white/80 px-4 py-3 backdrop-blur-sm">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl bg-white p-1.5 shadow-sm ring-1 ring-slate-200 focus-within:ring-2 focus-within:ring-indigo-400">
        <textarea
          ref={ref}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey) return;
            // 変換確定のEnterで送らない。keyCode 229 は古い実装への保険
            if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
            e.preventDefault();
            submit();
          }}
          placeholder="例）値引きを求められたときはどう対応した？"
          aria-label="AIへの質問"
          className="max-h-[200px] min-h-[2.25rem] flex-1 resize-none bg-transparent px-2.5 py-1.5
                     text-[15px] leading-6 text-slate-900 outline-none placeholder:text-slate-400"
        />
        {/* 送信ボタンの隣に置く。話す→直す→送るの順に手が動くため */}
        <MicButton
          onTranscribed={appendSpoken}
          disabled={busy}
          transcribe={transcribeChatQuestion}
        />

        {busy ? (
          <button
            onClick={onStop}
            aria-label="生成を中止する"
            className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-slate-900
                       text-white transition-colors hover:bg-slate-700"
          >
            <span className="block size-2.5 rounded-[3px] bg-white" />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!text.trim()}
            aria-label="送信する"
            className="flex size-9 shrink-0 items-center justify-center rounded-xl
                       bg-indigo-600 text-white transition-colors hover:bg-indigo-500
                       disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
          >
            <svg viewBox="0 0 16 16" className="size-4" aria-hidden="true">
              <path
                d="M8 13V3.5M8 3.5 3.75 7.75M8 3.5l4.25 4.25"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        )}
      </div>
      <p className="mx-auto mt-1.5 max-w-3xl px-1 text-[11px] text-slate-400">
        {busy ? (
          <span className="flex items-center gap-1.5">
            <Spinner />
            回答中です。中止して質問し直せます
          </span>
        ) : (
          "Enterで送信、Shift+Enterで改行。マイクで話しても入力できます"
        )}
      </p>
    </div>
  );
}
