/**
 * 登録し終わった瞬間の祝い。
 *
 * **貯める作業には見返りが無い。** 承認したナレッジが誰かの役に立つのは、
 * ずっと後の、本人には見えないところでのこと。それでも出し切った瞬間だけは、
 * 終わったと分かる形にしておきたい。次に持ってくる気になるかどうかは、
 * たぶんここで決まる。
 *
 * **1件ごとには祝わない。** 途中で祝われると、まだ残っているのに
 * 終わったように見える。呼ぶのは下書きを出し切ったときだけ（KnowledgeInput）。
 *
 * **操作を邪魔しない。** 全面に出しても `pointer-events-none` で、
 * 下のボタンはそのまま押せる。数秒で自分から消える。
 *
 * **動きを止める設定なら紙吹雪は出さない。** 紙吹雪は動きが本体で、
 * 止めると画面に固まった色紙が残るだけになる。件数を伝える札は残す
 * （動かさないことと、伝えないことは別）。
 */

import { useEffect, useMemo, useRef, type CSSProperties } from "react";

const PIECE_COUNT = 30;

/** 出てから消えるまで。長いと次の操作の邪魔になり、短いと見逃す */
const DURATION_MS = 2600;

/** 色は画面の既存のアクセントから借りる。知らない色が出ると別アプリの通知に見える */
const COLORS = ["#6366f1", "#818cf8", "#fbbf24", "#f97316", "#34d399", "#f472b6"];

type Piece = {
  left: number;
  delay: number;
  duration: number;
  color: string;
  width: number;
  height: number;
  round: boolean;
  drift: number;
  spin: number;
};

function makePieces(): Piece[] {
  return Array.from({ length: PIECE_COUNT }, () => {
    const size = 6 + Math.random() * 6;
    return {
      left: Math.random() * 100,
      delay: Math.random() * 600,
      // 落ちる速さをばらけさせる。揃っていると紙ではなくバーに見える
      duration: 1500 + Math.random() * 900,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      width: size,
      height: size * (Math.random() < 0.5 ? 1 : 1.8),
      round: Math.random() < 0.3,
      drift: (Math.random() - 0.5) * 220,
      spin: (Math.random() < 0.5 ? -1 : 1) * (360 + Math.random() * 540),
    };
  });
}

export function Celebration({ count, onDone }: { count: number; onDone: () => void }) {
  // 位置と色は1回だけ決める。再描画のたびに引き直すと、落下の途中で瞬間移動する
  const pieces = useMemo(() => makePieces(), []);

  // **消す合図は ref 越しに呼ぶ。** onDone を依存に置くと、親が再描画する
  // たびにタイマーが張り直され、いつまでも消えない
  const done = useRef(onDone);
  useEffect(() => {
    done.current = onDone;
  }, [onDone]);

  useEffect(() => {
    const timer = window.setTimeout(() => done.current(), DURATION_MS);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <div
      className="pointer-events-none fixed inset-0 z-50 overflow-hidden"
      role="status"
      aria-live="polite"
    >
      <div className="absolute inset-0 motion-reduce:hidden" aria-hidden="true">
        {pieces.map((p, i) => (
          <span
            key={i}
            className="celebrate-piece absolute top-0 block"
            style={
              {
                left: `${p.left}%`,
                width: p.width,
                height: p.height,
                backgroundColor: p.color,
                borderRadius: p.round ? "9999px" : "2px",
                animationDelay: `${p.delay}ms`,
                animationDuration: `${p.duration}ms`,
                "--drift": `${p.drift}px`,
                "--spin": `${p.spin}deg`,
              } as CSSProperties
            }
          />
        ))}
      </div>

      {/* 件数はここでだけ大きく出す。下に残る文言は後から読み返すためのもので、
          こちらは「終わった」というその瞬間のためのもの */}
      <div className="absolute inset-x-0 top-24 flex justify-center px-4">
        <p
          className="celebrate-badge rounded-2xl bg-white px-5 py-3 text-center text-sm
                     font-medium text-slate-800 shadow-lg ring-1 ring-indigo-200"
          style={{ animationDuration: `${DURATION_MS}ms` }}
        >
          <span className="mr-2 text-base">🎉</span>
          {count}件、登録できました
        </p>
      </div>
    </div>
  );
}
