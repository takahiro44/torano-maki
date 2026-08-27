/**
 * 調査ビューに住んでいるアシスタント。プロダクト名（AI虎の巻）の主。
 *
 * **虎とロボットを選べる。** どちらが良いかは好みで、正解が無かった。
 * 姿だけを差し替えられるようにして、居場所・気分・動きの仕組みは
 * 共通のままにしてある（表情の出し方が2つに分かれると必ず食い違う）。
 * 虎は生きもので、機械の部品を持たない。混ぜるとどちらでもない
 * メカ猫になるので、ロボットにしたい人はロボットの姿を選ぶ。
 *
 * **説明はしない。** 何を検索して何件当たったかはログとグラフが正確に出している。
 * ここで同じことを繰り返すと画面が二重になるだけなので、この子は
 * 「今どんな気分か」しか喋らない。数十秒の待ち時間に人の顔が一つあると、
 * 止まっているのか動いているのかを読み取る負担がぐっと減る。
 *
 * **セリフはフェーズから選ぶだけ。** AIに喋らせると、待たせている最中に
 * さらにLLMを呼ぶことになり、しかも内容を保証できない。
 *
 * **掴んで動かせて、消せる。** 歩き回るものが常にいるのを邪魔に感じる人が
 * いる。置き場所と表示の有無は lib/pet.ts が覚えていて、掴んで動かした
 * あとは徘徊せずそこに居続ける（動かしたということは、そこに居てほしい
 * ということなので）。
 */

import { useEffect, useRef, useState } from "react";
import {
  setPetHidden,
  setPetSkin,
  setPetSpot,
  usePetHidden,
  usePetSkin,
  usePetSpot,
} from "../../lib/pet";
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

/** これ以内の動きは「つついた」とみなす。掴んだつもりのない微動で位置を固定しない */
const DRAG_SLOP_PX = 4;

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
  const hidden = usePetHidden();
  const skin = usePetSkin();
  const spot = usePetSpot();
  const chased = useChase(phase);
  const drag = useDrag();

  useEffect(() => {
    const timer = window.setInterval(() => setIndex((n) => n + 1), TALK_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const mood = moodOf(phase, foundCount);
  const lines = LINES[mood];
  const line = lines[index % lines.length];

  if (hidden) return null;

  // 掴んで置かれた場所が最優先。次に、いま掴んでいる指の位置
  const pinned = drag.point ?? spot;
  const place: Place | null = pinned
    ? { x: pinned.x, y: pinned.y, facing: 0, flipped: pinned.x + PET_SIZE + BUBBLE_W > window.innerWidth }
    : chased;
  if (!place) return null;

  return (
    <div
      // **虎の立ち位置を transform に持たせる。** 吹き出しを並べて
      // 左右を入れ替える形にすると、セリフの長さで虎の位置がずれ、
      // 指したい先から離れてしまう。吹き出しは虎からぶら下げる
      className={
        "agent-pet-anchor group pointer-events-none fixed top-0 left-0 z-40 size-16 " +
        // 掴んでいる間は指に遅れずついてくる必要がある。歩く動きは離してから
        (drag.point ? "" : "transition-transform duration-700 ease-out motion-reduce:transition-none")
      }
      style={{ transform: `translate(${place.x}px, ${place.y}px)` }}
    >
      <button
        type="button"
        // つつくと次のことを言う。触れる相手だと分かるだけで、
        // 待っている間の画面が「見るもの」から「居るもの」になる。
        // 掴んで動かすと、そこに居続ける
        onPointerDown={drag.onPointerDown}
        onClick={() => {
          if (drag.movedRef.current) return;
          setIndex((n) => n + 1);
        }}
        aria-label="アシスタント（つつく・ドラッグで移動）"
        className={
          "agent-pet pointer-events-auto touch-none " +
          (drag.point ? "cursor-grabbing" : "cursor-grab")
        }
      >
        {skin === "tiger" ? (
          <Tiger mood={mood} facing={place.facing} />
        ) : (
          <Robot mood={mood} facing={place.facing} />
        )}
      </button>

      {/* しまうボタン。常に出しておくと顔の一部のように見えるので、
          近づいたときだけ現れる */}
      <button
        type="button"
        onClick={() => setPetHidden(true)}
        aria-label="アシスタントをしまう"
        title="アシスタントをしまう"
        className="pointer-events-auto absolute -top-1 -right-1 flex size-5 items-center justify-center
                   rounded-full bg-white text-[11px] leading-none text-slate-400 opacity-0 shadow-sm
                   ring-1 ring-slate-200/80 transition-opacity hover:text-slate-700
                   group-hover:opacity-100 focus-visible:opacity-100"
      >
        ✕
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
        {/* 操作は近づいたときだけ出す。常に出しておくとセリフより目立ち、
            吹き出しが操作パネルになってしまう（✕ と同じ扱い） */}
        <div
          className="mt-1 flex flex-wrap gap-x-2 text-[10px] opacity-0 transition-opacity
                     group-hover:opacity-100 focus-within:opacity-100"
        >
          <button
            type="button"
            onClick={() => setPetSkin(skin === "tiger" ? "robot" : "tiger")}
            className="pointer-events-auto text-indigo-500 hover:text-indigo-400"
          >
            {skin === "tiger" ? "ロボットにする" : "虎にする"}
          </button>
          {spot && (
            <button
              type="button"
              onClick={() => setPetSpot(null)}
              className="pointer-events-auto text-indigo-500 hover:text-indigo-400"
            >
              またついていく
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * 掴んで動かす。
 *
 * **離した場所を覚えるのは離したとき。** 動かしている最中に覚えると、
 * 指を戻して「やっぱりやめた」ができない。
 *
 * **`setPointerCapture` を使う。** 掴んだまま素早く動かすとポインタが
 * 虎から外れる。捕まえておかないと、そこで置き去りになる。
 */
function useDrag() {
  const [point, setPoint] = useState<{ x: number; y: number } | null>(null);
  // クリックとドラッグの区別。押した位置から動いたかどうかで決める
  const movedRef = useRef(false);

  function onPointerDown(e: React.PointerEvent<HTMLButtonElement>) {
    const target = e.currentTarget;
    const rect = target.getBoundingClientRect();
    const grabX = e.clientX - rect.left;
    const grabY = e.clientY - rect.top;
    const startX = e.clientX;
    const startY = e.clientY;
    movedRef.current = false;
    target.setPointerCapture(e.pointerId);

    const move = (ev: PointerEvent) => {
      if (
        Math.abs(ev.clientX - startX) > DRAG_SLOP_PX ||
        Math.abs(ev.clientY - startY) > DRAG_SLOP_PX
      ) {
        movedRef.current = true;
      }
      if (!movedRef.current) return;
      setPoint({
        x: clamp(ev.clientX - grabX, EDGE_PAD, window.innerWidth - PET_SIZE - EDGE_PAD),
        y: clamp(ev.clientY - grabY, TOP_SAFE, window.innerHeight - PET_SIZE - EDGE_PAD),
      });
    };

    const up = (ev: PointerEvent) => {
      target.releasePointerCapture?.(ev.pointerId);
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", up);
      setPoint((current) => {
        if (movedRef.current && current) setPetSpot(current);
        return null;
      });
    };

    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", up);
  }

  // ref をそのまま返す。描画中に読むと更新に追従しない（onClick の中で読む）
  return { point, movedRef, onPointerDown };
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

/**
 * 虎の目。色を変えるより、形の方が小さくても伝わる。
 *
 * **白目を描かない。** 64pxでは白目が潰れて濁った点になり、生気が消える。
 * 琥珀の虹彩と黒い瞳孔、そこに小さなハイライトを1つ置くだけでよい。
 *
 * **眉斑と鼻筋はここに描かない。** あれは顔の模様で動かない。目だけが
 * 進む方を向いて動く。一緒に動かすと顔ごと横にずれて見える。
 */
function TigerEyes({ mood }: { mood: Mood }) {
  const scanning = mood === "searching";

  if (mood === "happy" || mood === "done") {
    return (
      <g stroke="#1e293b" strokeWidth={2.2} strokeLinecap="round" fill="none">
        <path d="M17.8 26.6c1.6-2.9 4.2-2.9 5.8 0" />
        <path d="M30.4 26.6c1.6-2.9 4.2-2.9 5.8 0" />
      </g>
    );
  }
  if (mood === "sad") {
    return (
      <g stroke="#1e293b" strokeWidth={2.2} strokeLinecap="round" fill="none">
        <path d="M17.8 24.8c1.6 2.9 4.2 2.9 5.8 0" />
        <path d="M30.4 24.8c1.6 2.9 4.2 2.9 5.8 0" />
      </g>
    );
  }
  return (
    <g className={scanning ? "agent-pet-scan" : "agent-pet-eye"}>
      <circle cx={20.7} cy={25.8} r={3.2} fill="#fbbf24" />
      <circle cx={33.3} cy={25.8} r={3.2} fill="#fbbf24" />
      <circle cx={20.7} cy={25.8} r={1.8} fill="#1e293b" />
      <circle cx={33.3} cy={25.8} r={1.8} fill="#1e293b" />
      {/* ハイライト。これが無いと、描いた丸であって生きている目に見えない */}
      <circle cx={21.6} cy={24.8} r={0.75} fill="#fffbeb" />
      <circle cx={34.2} cy={24.8} r={0.75} fill="#fffbeb" />
    </g>
  );
}

/**
 * 虎。
 *
 * **虎に見せる3つ**（丸くて小さい耳・太い黒縞・目の上の白い眉斑）を外さない。
 * 耳を尖らせる、縞を細くする、眉斑を省く——どれか1つでも崩すと猫になる。
 *
 * **忙しさは尻尾で出す。** 生きものなので光る部品を足せない。尻尾が揺れて
 * いるかどうかだけで、待っている側には十分伝わる（index.css）。
 *
 * 64pxで描くので、これ以上の作り込みは潰れて効かない。
 */
function Tiger({ mood, facing }: { mood: Mood; facing: number }) {
  const busy = mood === "searching" || mood === "thinking" || mood === "writing";

  return (
    <svg viewBox="0 0 54 50" className="size-16 drop-shadow-sm" aria-hidden="true">
      <defs>
        <linearGradient id="agent-pet-fur" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#fbbf24" />
          <stop offset="55%" stopColor="#f97316" />
          <stop offset="100%" stopColor="#ea580c" />
        </linearGradient>
      </defs>

      {/* 尻尾。忙しいときだけ揺れる。縞ごと1つの g で回す
          （別々に回すとそれぞれの中心で回って崩れる） */}
      <g className={busy ? "agent-pet-tail" : undefined}>
        <path
          d="M42.5 38.5c7 2 9.5-3.4 6.4-8.6"
          fill="none"
          stroke="#f97316"
          strokeWidth={3.6}
          strokeLinecap="round"
        />
        <g stroke="#1e293b" strokeWidth={1.7} strokeLinecap="round" opacity={0.85}>
          <path d="M46.3 38.2l.9-1.7" />
          <path d="M48.8 35l1.4-1.1" />
        </g>
        {/* 先端は白。虎の尻尾はここが白い */}
        <circle cx={49.5} cy={29.6} r={1.7} fill="#fff7ed" />
      </g>

      {/* 耳。**丸く、小さく、離して置く。** 尖らせると猫になる */}
      <circle cx={13.6} cy={15.4} r={6.3} fill="url(#agent-pet-fur)" />
      <circle cx={40.4} cy={15.4} r={6.3} fill="url(#agent-pet-fur)" />
      <circle cx={13.6} cy={16.1} r={3.4} fill="#fda4af" opacity={0.8} />
      <circle cx={40.4} cy={16.1} r={3.4} fill="#fda4af" opacity={0.8} />

      {/* 顔。頬をわずかに張り出させる */}
      <path
        d="M27 10.5c9.6 0 15.7 4.9 16.7 12.2.3 2 1.9 2.6 1.9 4.2 0 1.7-1.7 2.3-2 4.2C42.5 38.6 36.1 43.6 27 43.6S11.5 38.6 10.4 31.1c-.3-1.9-2-2.5-2-4.2 0-1.6 1.6-2.2 1.9-4.2C11.3 15.4 17.4 10.5 27 10.5z"
        fill="url(#agent-pet-fur)"
      />

      {/* 額の縞。太く、黒く、左右対称に。ここが一番「虎」を決める */}
      <g stroke="#1e293b" strokeWidth={2.6} strokeLinecap="round" fill="none" opacity={0.9}>
        <path d="M27 12.2v5.4" />
        <path d="M22.2 13.1c-.8 1.8-1 3.2-.7 4.8" />
        <path d="M31.8 13.1c.8 1.8 1 3.2.7 4.8" />
        <path d="M17.4 15.9c-1 1.3-1.5 2.6-1.5 4" />
        <path d="M36.6 15.9c1 1.3 1.5 2.6 1.5 4" />
      </g>

      {/* 目の上の白い眉斑。これが無いと、縞をいくら足しても猫の顔に見える */}
      <g fill="#fffbeb" opacity={0.92}>
        <ellipse cx={20.5} cy={22.2} rx={4.7} ry={2.5} />
        <ellipse cx={33.5} cy={22.2} rx={4.7} ry={2.5} />
      </g>

      {/* 進む方を見る。目が動くだけで、勝手に歩いたのではなく
          自分で行き先を決めたように見える */}
      <g
        transform={`translate(${facing * 1.6} 0)`}
        style={{ transition: "transform 600ms ease-out" }}
      >
        <TigerEyes mood={mood} />
      </g>

      {/* 頬の縞。輪郭へ向かって流れる向きに引く */}
      <g stroke="#1e293b" strokeWidth={2.4} strokeLinecap="round" fill="none" opacity={0.88}>
        <path d="M10.6 26.4c1.6.6 3.2.9 4.8.9" />
        <path d="M10.2 31.4c1.8.5 3.6.7 5.4.6" />
        <path d="M11.8 36c1.7.2 3.3.1 4.9-.2" />
        <path d="M43.4 26.4c-1.6.6-3.2.9-4.8.9" />
        <path d="M43.8 31.4c-1.8.5-3.6.7-5.4.6" />
        <path d="M42.2 36c-1.7.2-3.3.1-4.9-.2" />
      </g>

      {/* 口元。虎は口の周りが白く広い */}
      <path
        d="M27 30.2c5.9 0 10.6 2.4 10.6 6.5 0 4.3-4.7 7.1-10.6 7.1s-10.6-2.8-10.6-7.1c0-4.1 4.7-6.5 10.6-6.5z"
        fill="#fffbeb"
      />
      {/* 鼻 */}
      <path
        d="M23.9 32.3h6.2c.8 0 1.2.9.6 1.4l-2.7 2.4a1.6 1.6 0 0 1-2 0l-2.7-2.4c-.6-.5-.2-1.4.6-1.4z"
        fill="#fb7185"
      />
      {/* 口。鼻筋を下ろして左右へ広げる */}
      <g stroke="#1e293b" strokeWidth={1.2} strokeLinecap="round" fill="none" opacity={0.8}>
        <path d="M27 36.4v1.9" />
        <path d="M27 38.3c-1.3 1.6-3.4 1.5-4.4.1" />
        <path d="M27 38.3c1.3 1.6 3.4 1.5 4.4.1" />
      </g>

      {/* ひげ */}
      <g stroke="#fff7ed" strokeWidth={0.9} strokeLinecap="round">
        <path d="M17.4 34.2 9.2 32.4" />
        <path d="M17.2 37.4 9.2 38.6" />
        <path d="M36.6 34.2 44.8 32.4" />
        <path d="M36.8 37.4 44.8 38.6" />
      </g>
    </svg>
  );
}

/** ロボットの目。虎とは目の位置も形も違うので、気分の判定だけ共有して形は別に持つ */
function RobotEyes({ mood }: { mood: Mood }) {
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

/** 初代の姿。虎に置き換えたあとも選べるように残してある */
function Robot({ mood, facing }: { mood: Mood; facing: number }) {
  const busy = mood === "searching" || mood === "thinking" || mood === "writing";

  return (
    <svg viewBox="0 0 54 50" className="size-16 drop-shadow-sm" aria-hidden="true">
      <defs>
        <linearGradient id="agent-pet-robot-body" x1="0" y1="0" x2="0" y2="1">
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

      <rect x={9} y={12} width={36} height={30} rx={11} fill="url(#agent-pet-robot-body)" />
      <rect x={14} y={20} width={26} height={16} rx={8} fill="#1e1b4b" />
      {/* 進む方を見る。目が動くだけで、勝手に歩いたのではなく
          自分で行き先を決めたように見える */}
      <g
        transform={`translate(${facing * 1.6} 0)`}
        style={{ transition: "transform 600ms ease-out" }}
      >
        <RobotEyes mood={mood} />
      </g>
    </svg>
  );
}
