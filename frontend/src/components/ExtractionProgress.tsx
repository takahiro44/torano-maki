/**
 * 抽出を待っている間の表示。
 *
 * **進捗を演じない。** `/ingest/text` は途中経過を返さないので、
 * 段階が進むバーや「解析中 → 構造化中」の切り替えを出すと、それは
 * 動いているように見せるための嘘になる（AgentWorkspace が走査中に
 * 実在のナレッジだけを流しているのと同じ理由）。
 *
 * **出せるのは、送った原文と経過時間だけ。** その上を光が一定の速さで
 * 往復する。読んでいる最中であることは伝わり、どこまで進んだかは
 * 何も言っていない。長文ほど時間がかかるという事実は、原文の量が
 * そのまま見えていることで伝わる。
 *
 * **経過時間を出す。** 1分近く待つことがあり、止まったのか動いているのかを
 * 判断する材料が無いと、人は再読込してしまう。
 */

import { useEffect, useState } from "react";

/** 待たせすぎの目安。これを超えたら、まだ諦めなくてよいことを伝える */
const LONG_WAIT_SEC = 45;

export function ExtractionProgress({ text }: { text: string }) {
  const seconds = useElapsedSeconds();

  return (
    <div
      data-pet-anchor="ingest-progress"
      className="overflow-hidden rounded-2xl bg-white ring-1 ring-indigo-200"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-indigo-100 px-4 py-2.5">
        <p className="text-sm font-medium text-indigo-900">原文を読んでいます</p>
        <p className="font-mono text-[11px] text-slate-400">
          {text.length}文字 ・ {seconds}秒
        </p>
      </div>

      {/* 送った原文そのもの。ここに映っているのが、いま読まれているもの */}
      <div className="relative max-h-40 overflow-hidden px-4 py-3">
        <p className="agent-scan-dim whitespace-pre-wrap text-[13px] leading-6 text-slate-600">
          {text}
        </p>
        {/* 光は原文の上を往復するだけ。どこまで読んだかは指していない */}
        <div
          aria-hidden="true"
          className="agent-scan-sweep pointer-events-none absolute inset-y-0 -left-1/3 w-1/3
                     bg-gradient-to-r from-transparent via-indigo-200/50 to-transparent"
        />
        {/* 下端をぼかす。切れているのか終わっているのかを見分けさせる */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-white to-transparent"
        />
      </div>

      <p className="border-t border-indigo-100 px-4 py-2 text-[11px] leading-relaxed text-slate-400">
        {seconds >= LONG_WAIT_SEC
          ? "長い原文はいくつかに分けて読むため、1分を超えることがあります。まだ動いています。"
          : "AIが構造化しています。終わると、承認する前に内容を直せます。"}
      </p>
    </div>
  );
}

/**
 * 経過秒数。
 *
 * **マウントからの実時間で数える。** 1秒ごとに数を足すやり方だと、
 * タブが裏に回っている間タイマーが間引かれ、戻ったときに実際より
 * 短い秒数が出る（待たされた実感と食い違う）。
 */
function useElapsedSeconds(): number {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const startedAt = Date.now();
    const timer = window.setInterval(
      () => setSeconds(Math.floor((Date.now() - startedAt) / 1000)),
      500,
    );
    return () => window.clearInterval(timer);
  }, []);

  return seconds;
}
