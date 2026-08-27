/**
 * ナレッジの面（AgentWorkspace）と、その開閉タブ。
 *
 * **チャットと登録で同じ部品を使う。** どちらの画面でも「蓄積に何が入っているか」を
 * 見ながら作業する。登録の側では、書こうとしている話が既に有るかどうかを
 * その場で確かめられることが効く。見た目や開閉の作法をそれぞれの画面で
 * 書くと、同じ面なのに幅も操作も違うものが2つできる（CLAUDE.md 6章）。
 *
 * **開閉は画面ごとに覚える。** チャットでは畳んで会話を広く使い、登録では
 * 開いて照らし合わせたい、という使い分けが成り立つ。1つの鍵で覚えると
 * 片方の都合がもう片方を巻き込む。
 *
 * **横幅の足りない画面では出さない。** 主の列（会話・入力欄）が細くなる方が
 * 損失として大きい。
 */

import { useEffect, useState } from "react";
import { AgentWorkspace } from "./AgentWorkspace";
import type { Turn } from "./useChat";

type Props = {
  /** 開閉を覚える鍵。画面ごとに別の値を渡すこと */
  storageKey: string;
  /** 調査の経過を映す対象。登録画面のように調査が無い場所では null */
  turn?: Turn | null;
  /** 待機中の一覧の再取得トリガー */
  reloadKey: number;
};

export function WorkspacePane({ storageKey, turn = null, reloadKey }: Props) {
  const [open, setOpen] = useState(() => loadOpen(storageKey));

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, open ? "1" : "0");
    } catch {
      // 覚えられなくても開閉そのものは動く
    }
  }, [storageKey, open]);

  return (
    <div className="hidden shrink-0 lg:flex">
      {open && (
        <div className="w-[360px] xl:w-[420px]">
          <AgentWorkspace turn={turn} reloadKey={reloadKey} />
        </div>
      )}
      {/* 三角タブは開閉どちらでも常に出す。同じボタンで出し入れできる */}
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "ナレッジの面を閉じる" : "ナレッジの面を開く"}
        aria-expanded={open}
        className="flex w-5 shrink-0 items-center justify-center border-r border-indigo-100
                   bg-indigo-50/40 text-sm text-slate-300 hover:bg-indigo-100/60 hover:text-slate-600"
      >
        {open ? "◀" : "▶"}
      </button>
    </div>
  );
}

function loadOpen(storageKey: string): boolean {
  try {
    return localStorage.getItem(storageKey) !== "0";
  } catch {
    // プライベートモードで読めないことがある。既定（開く）で始めればよい
    return true;
  }
}
