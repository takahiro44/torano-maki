/**
 * ナビの中の「ホバーまたはクリックでサブメニューを出す」タブ群。
 * 「音声」「登録」のように、ルートは別だが利用者の意図としては
 * 1つの見出し（ナレッジ登録）にまとめたい場合に使う。
 *
 * **mouseleaveはラッパー側に付ける。** トリガーボタン単体に付けると、
 * メニューへマウスを移動する途中で一旦離れたことになり閉じてしまう。
 *
 * **選択時に明示的に閉じる。** navigate() は同じパスへの遷移では何もしない
 * （lib/router.ts）ため、ルート変化をトリガーに閉じる作りにはできない。
 */

import { useState } from "react";

type Props<T extends string> = {
  label: string;
  items: { key: T; label: string }[];
  active: boolean;
  onNavigate: (key: T) => void;
};

export function NavGroup<T extends string>({ label, items, active, onNavigate }: Props<T>) {
  const [open, setOpen] = useState(false);

  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-current={active ? "page" : undefined}
        className={
          "-mb-px flex items-center gap-1 border-b-2 px-3 py-2 text-sm font-medium transition-colors " +
          (active
            ? "border-indigo-600 text-slate-900"
            : "border-transparent text-slate-400 hover:text-slate-600")
        }
      >
        {label}
        <Chevron open={open} />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-10 mt-1 min-w-40 rounded-lg bg-white py-1 shadow-lg ring-1 ring-slate-200">
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => {
                onNavigate(item.key);
                setOpen(false);
              }}
              className="block w-full px-3 py-1.5 text-left text-sm text-slate-600 hover:bg-slate-50 hover:text-slate-900"
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 12 12"
      aria-hidden="true"
      className={"size-3 text-slate-400 transition-transform " + (open ? "rotate-180" : "")}
    >
      <path
        d="M2.5 4.5 6 8l3.5-3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
