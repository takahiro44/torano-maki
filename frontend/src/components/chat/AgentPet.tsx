/**
 * 調査ビューに住んでいるロボット。
 *
 * **説明はしない。** 何を検索して何件当たったかはログとグラフが正確に出している。
 * ここで同じことを繰り返すと画面が二重になるだけなので、この子は
 * 「今どんな気分か」しか喋らない。数十秒の待ち時間に人の顔が一つあると、
 * 止まっているのか動いているのかを読み取る負担がぐっと減る。
 *
 * **セリフはフェーズから選ぶだけ。** AIに喋らせると、待たせている最中に
 * さらにLLMを呼ぶことになり、しかも内容を保証できない。
 */

import { useEffect, useRef, useState } from "react";
import type { Phase } from "./phase";

/** セリフの入れ替え間隔。黙りすぎず、読み終わる前に消えない長さ */
const TALK_INTERVAL_MS = 5200;

/**
 * 見てほしいものにつく目印の名前。
 *
 * **要素の位置をReactのstateで配らない。** 注目先はグラフの中の点だったり
 * 会話の本文だったりと持ち主がばらばらで、座標を上へ集めると、力学計算の
 * 1フレームごとに画面全体が描き直される。目印だけDOMに置いておけば、
 * 寄っていく側が自分のペースで読みに来られる。
 */
const ANCHORS: Record<Phase, string[]> = {
  // 開いている札が最優先。利用者がいま見ているものと同じ場所に立つ
  idle: ["popup", "composer", "focus"],
  planning: ["popup", "focus", "composer"],
  searching: ["popup", "focus", "composer"],
  answering: ["popup", "answer", "focus"],
  done: ["popup", "citations", "answer", "focus"],
  error: ["popup", "answer", "composer"],
};

/** 目印を読みに行く間隔。歩いて寄る動きなので、毎フレーム追う必要はない */
const CHASE_MS = 260;

/** ロボットの見た目の大きさ */
const PET_SIZE = 64;
const BUBBLE_W = 160;
const EDGE_PAD = 10;

/**
 * 上端の安全域。タブと見出しの帯にかぶらせない。
 *
 * 会話が伸びると回答の要素は上へ流れていき、`top` が負になる。素直に従うと
 * ヘッダーの上に立ってしまう。
 */
const TOP_SAFE = 96;

/** 目印がこれだけ画面に見えていなければ、次の候補へ回す */
const MIN_VISIBLE = 24;

type Mood = "idle" | "thinking" | "searching" | "happy" | "writing" | "done" | "sad";

const LINES: Record<Mood, string[]> = {
  idle: ["ひまだな〜", "なんでも聞いてね", "準備はできてるよ", "待機ちゅう…"],
  thinking: ["うーん、どこから探そう", "ちょっと考えさせて", "たしかこの辺に…"],
  searching: ["探してる探してる！", "お、なんかありそう", "もうちょい待ってね"],
  happy: ["みつけた！", "お、当たりっぽい", "これ、いいかも"],
  // 終わったあとに「みつけた！」と言い続けると、まだ探しているように見える
  done: ["できたよ！どうかな", "こんな感じでどう？", "また聞いてね", "ふぅ、ひと仕事"],
  writing: ["いまカタカタ書いてる", "もうすぐできるよ", "あとちょっと！"],
  sad: ["あれ、うまくいかなかった…", "ごめん、もう一回だけ", "ちょっと調子わるいかも"],
};

/**
 * 検索が終わった直後は「考え中」ではなく「見つけた」にする。
 *
 * Tool が終わるとフェーズは planning に戻るが、利用者から見ればそこは
 * 成果が出た瞬間であり、そこで「うーん」と言われると探せなかったように見える。
 */
function moodOf(phase: Phase, foundCount: number): Mood {
  switch (phase) {
    case "idle":
      return "idle";
    case "planning":
      return foundCount > 0 ? "happy" : "thinking";
    case "searching":
      return "searching";
    case "answering":
      return "writing";
    case "done":
      return "done";
    case "error":
      return "sad";
  }
}

export function AgentPet({ phase, foundCount }: { phase: Phase; foundCount: number }) {
  // セリフの番号だけを持つ。気分が変わっても番号は据え置きでよく、
  // リセットしないぶん「同じ台詞に戻る」感じが出ない
  const [index, setIndex] = useState(0);
  const place = useChase(phase);

  useEffect(() => {
    const timer = window.setInterval(() => setIndex((n) => n + 1), TALK_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const mood = moodOf(phase, foundCount);
  const lines = LINES[mood];
  const line = lines[index % lines.length];

  if (!place) return null;

  return (
    <div
      // **ロボットの立ち位置を transform に持たせる。** 吹き出しを並べて
      // 左右を入れ替える形にすると、セリフの長さでロボットの位置がずれ、
      // 指したい先から離れてしまう。吹き出しはロボットからぶら下げる
      className="agent-pet-anchor pointer-events-none fixed top-0 left-0 z-40 size-16 transition-transform duration-700 ease-out motion-reduce:transition-none"
      style={{ transform: `translate(${place.x}px, ${place.y}px)` }}
    >
      <button
        type="button"
        // つつくと次のことを言う。触れる相手だと分かるだけで、
        // 待っている間の画面が「見るもの」から「居るもの」になる
        onClick={() => setIndex((n) => n + 1)}
        aria-label="アシスタントをつつく"
        className="agent-pet pointer-events-auto"
      >
        <Robot mood={mood} facing={place.facing} />
      </button>

      <div
        className={
          "absolute top-1/2 w-max max-w-[160px] -translate-y-1/2 rounded-2xl bg-white px-3 py-2 shadow-md ring-1 ring-slate-200/80 " +
          (place.flipped ? "right-full mr-2" : "left-full ml-2")
        }
      >
        {/* 吹き出しの尻尾。誰が喋っているのかを線で結ぶ */}
        <span
          className={
            "absolute top-1/2 -mt-1 size-2 rotate-45 bg-white " +
            (place.flipped
              ? "-right-[3px] border-t border-r border-slate-200/80"
              : "-left-[3px] border-b border-l border-slate-200/80")
          }
        />
        <p key={`${mood}:${index}`} className="agent-rise text-xs leading-snug text-slate-600">
          {line}
        </p>
      </div>
    </div>
  );
}

type Place = { x: number; y: number; facing: number; flipped: boolean };

/**
 * 目印を探して、その隣まで歩く。
 *
 * **重ならない位置に立つ。** 見てほしいものの上に乗ってしまっては本末転倒なので、
 * 左に余白があれば左、無ければ右に立つ。画面の右寄りにいるときは
 * 吹き出しを左に出す（そのままだと画面の外へ出て、黙ったように見える）。
 */
function useChase(phase: Phase): Place | null {
  const [place, setPlace] = useState<Place | null>(null);
  // 行き先を読むのはタイマーの中。その時点のフェーズを見に行けるようにする
  const phaseRef = useRef(phase);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    const chase = () => {
      const found = findAnchor(ANCHORS[phaseRef.current]);
      if (!found) return;
      const { rect, name } = found;

      // 幅のある要素は左肩あたり、点の目印はその点そのものを狙う
      const targetX = rect.left + (rect.width > 4 ? 10 : 0);
      // **見えている範囲の中を狙う。** 長い回答は上へはみ出していることがあり、
      // 要素の中心をそのまま使うと画面の外を指してしまう
      const visibleTop = Math.max(rect.top, TOP_SAFE);
      const visibleBottom = Math.min(rect.bottom, window.innerHeight - EDGE_PAD);
      const targetY = visibleTop + Math.min((visibleBottom - visibleTop) / 2, 44);

      // 開いた札の左は一覧が詰まっている。札のときだけ右の余白に立つ
      const leftSide = targetX - PET_SIZE - 16;
      const onLeft = name === "popup" ? false : leftSide > EDGE_PAD;
      const x = clamp(
        onLeft ? leftSide : name === "popup" ? rect.right + 16 : targetX + 18,
        EDGE_PAD,
        window.innerWidth - PET_SIZE - EDGE_PAD,
      );
      const y = clamp(targetY - PET_SIZE / 2, TOP_SAFE, window.innerHeight - PET_SIZE - EDGE_PAD);

      // **吹き出しは見てほしいものの反対側へ出す。** 指した先にセリフを
      // かぶせると、読ませたいものが読めなくなる（出典の1件目が隠れていた）
      const outward = onLeft ? x - BUBBLE_W - 8 > 0 : !(x + PET_SIZE + BUBBLE_W < window.innerWidth);

      setPlace((prev) => {
        if (prev && Math.abs(prev.x - x) < 4 && Math.abs(prev.y - y) < 4) return prev;
        return { x, y, facing: prev ? Math.sign(x - prev.x) : 0, flipped: outward };
      });
    };

    const timer = window.setInterval(chase, CHASE_MS);
    return () => window.clearInterval(timer);
  }, []);

  return place;
}

/** 先に見つかった目印を採用する。並び順がそのまま優先順位 */
function findAnchor(names: string[]): { rect: DOMRect; name: string } | null {
  for (const name of names) {
    const el = document.querySelector(`[data-pet-anchor="${name}"]`);
    if (!el) continue;
    const rect = el.getBoundingClientRect();
    // 別のタブを見ている間は display:none になり、すべて0で返る
    if (rect.top === 0 && rect.left === 0 && rect.width === 0) continue;
    // 上へ流れていった要素を指し続けると、画面の端で固まって見える
    const visible = Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, TOP_SAFE);
    if (rect.height > 0 && visible < MIN_VISIBLE) continue;
    return { rect, name };
  }
  return null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/** 目の形で気分を出す。色を変えるより、形の方が小さくても伝わる */
function Eyes({ mood }: { mood: Mood }) {
  const scanning = mood === "searching";

  if (mood === "happy" || mood === "done") {
    return (
      <g stroke="#a5f3fc" strokeWidth={2.2} strokeLinecap="round" fill="none">
        <path d="M18 29c1.5-2.5 4.5-2.5 6 0" />
        <path d="M30 29c1.5-2.5 4.5-2.5 6 0" />
      </g>
    );
  }
  if (mood === "sad") {
    return (
      <g stroke="#a5f3fc" strokeWidth={2.2} strokeLinecap="round" fill="none">
        <path d="M18 27c1.5 2.5 4.5 2.5 6 0" />
        <path d="M30 27c1.5 2.5 4.5 2.5 6 0" />
      </g>
    );
  }
  return (
    <g fill="#a5f3fc" className={scanning ? "agent-pet-scan" : "agent-pet-eye"}>
      <circle cx={21} cy={28} r={2.6} />
      <circle cx={33} cy={28} r={2.6} />
    </g>
  );
}

function Robot({ mood, facing }: { mood: Mood; facing: number }) {
  const busy = mood === "searching" || mood === "thinking" || mood === "writing";

  return (
    <svg viewBox="0 0 54 50" className="size-16 drop-shadow-sm" aria-hidden="true">
      <defs>
        <linearGradient id="agent-pet-body" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#818cf8" />
          <stop offset="100%" stopColor="#6366f1" />
        </linearGradient>
      </defs>

      {/* アンテナ。忙しいときだけ光る */}
      <path d="M27 12V6" stroke="#c7d2fe" strokeWidth={2} strokeLinecap="round" />
      <circle cx={27} cy={4.5} r={3} fill={busy ? "#f59e0b" : "#c7d2fe"}>
        {busy && <animate attributeName="opacity" values="1;0.3;1" dur="1.1s" repeatCount="indefinite" />}
      </circle>

      {/* 耳 */}
      <rect x={4} y={24} width={5} height={9} rx={2.5} fill="#c7d2fe" />
      <rect x={45} y={24} width={5} height={9} rx={2.5} fill="#c7d2fe" />

      <rect x={9} y={12} width={36} height={30} rx={11} fill="url(#agent-pet-body)" />
      <rect x={14} y={20} width={26} height={16} rx={8} fill="#1e1b4b" />
      {/* 進む方を見る。目が動くだけで、勝手に歩いたのではなく
          自分で行き先を決めたように見える */}
      <g
        transform={`translate(${facing * 1.6} 0)`}
        style={{ transition: "transform 600ms ease-out" }}
      >
        <Eyes mood={mood} />
      </g>
    </svg>
  );
}
