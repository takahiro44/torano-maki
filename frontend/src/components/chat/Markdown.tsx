/**
 * 回答本文のMarkdown描画。
 *
 * **なぜ自前で書くか。** react-markdown を入れれば正確に描けるが、
 * 依存の追加はチームの合意が要る（CLAUDE.md 3章）。合意を待つ間、
 * 表や番号付きリストが崩れたままになる方が損失が大きいと判断した。
 *
 * **行単位ではなくブロック単位で解釈する。** 以前の実装は1行ずつ見ていたため、
 * 連続する箇条書きを `<ul>` にまとめられず、表・コードブロック・番号付き
 * リストが崩れていた。モデルはこれらを平気で返してくる。
 *
 * **HTMLは解釈しない。** `dangerouslySetInnerHTML` を使わず、必ずReactの
 * 要素として組み立てる。LLMの出力をそのままHTMLとして描くと、蓄積された
 * ナレッジに混ざった文字列がそのままスクリプトになりうるため。
 */

import type { ReactNode } from "react";

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "code"; lang: string; code: string }
  | { kind: "quote"; lines: string[] }
  | { kind: "list"; ordered: boolean; items: ListItem[] }
  | { kind: "table"; header: string[]; rows: string[][] }
  | { kind: "rule" };

type ListItem = { text: string; children: Block[] };

const HEADING = /^(#{1,6})\s+(.*)$/;
const FENCE = /^```(\S*)\s*$/;
const BULLET = /^(\s*)[-*+]\s+(.*)$/;
const ORDERED = /^(\s*)\d+[.)]\s+(.*)$/;
const QUOTE = /^>\s?(.*)$/;
const RULE = /^(-{3,}|\*{3,}|_{3,})$/;
const TABLE_DIVIDER = /^\|?[\s:|-]*-[\s:|-]*\|?$/;

export function Markdown({ text }: { text: string }) {
  return <BlockList blocks={parseBlocks(text.split("\n"))} />;
}

function BlockList({ blocks }: { blocks: Block[] }) {
  return (
    <div className="space-y-3 text-[15px] leading-7 text-slate-700">
      {blocks.map((block, i) => (
        <BlockView key={i} block={block} />
      ))}
    </div>
  );
}

/** インデント幅。タブは2スペース相当として数える */
function indentOf(raw: string): number {
  const match = /^\s*/.exec(raw);
  return match ? match[0].replace(/\t/g, "  ").length : 0;
}

function parseBlocks(lines: string[]): Block[] {
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    // コードブロック。閉じ忘れは最後までをコードとして扱う
    const fence = FENCE.exec(trimmed);
    if (fence) {
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i].trim())) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1;
      blocks.push({ kind: "code", lang: fence[1], code: code.join("\n") });
      continue;
    }

    if (RULE.test(trimmed)) {
      blocks.push({ kind: "rule" });
      i += 1;
      continue;
    }

    const heading = HEADING.exec(trimmed);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      i += 1;
      continue;
    }

    // 表。区切り行が続いているかどうかでしか判別できない
    if (trimmed.includes("|") && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1].trim())) {
      const header = splitRow(trimmed);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(splitRow(lines[i].trim()));
        i += 1;
      }
      blocks.push({ kind: "table", header, rows });
      continue;
    }

    if (QUOTE.test(trimmed)) {
      const quoted: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i].trim())) {
        quoted.push((QUOTE.exec(lines[i].trim()) as RegExpExecArray)[1]);
        i += 1;
      }
      blocks.push({ kind: "quote", lines: quoted });
      continue;
    }

    // 箇条書き。連続する項目をひとつのリストにまとめる。
    // まとめないと項目ごとに間隔が空き、リストに見えなくなる
    const ordered = ORDERED.test(line);
    if (ordered || BULLET.test(line)) {
      const raw: { indent: number; text: string }[] = [];
      const pattern = ordered ? ORDERED : BULLET;
      while (i < lines.length) {
        const match = pattern.exec(lines[i]);
        if (match) {
          raw.push({ indent: indentOf(match[1]), text: match[2] });
          i += 1;
          continue;
        }
        // 継続行（箇条書きの続きの本文）は直前の項目にぶら下げる
        const next = lines[i];
        if (next.trim() && indentOf(next) > 0 && raw.length > 0) {
          raw[raw.length - 1].text += "\n" + next.trim();
          i += 1;
          continue;
        }
        break;
      }
      blocks.push({ kind: "list", ordered, items: nest(raw) });
      continue;
    }

    // 段落。空行または他のブロックが始まるまでをひとまとまりにする
    const paragraph: string[] = [];
    while (i < lines.length) {
      const current = lines[i];
      if (
        !current.trim() ||
        HEADING.test(current.trim()) ||
        FENCE.test(current.trim()) ||
        QUOTE.test(current.trim()) ||
        RULE.test(current.trim()) ||
        BULLET.test(current) ||
        ORDERED.test(current)
      ) {
        break;
      }
      paragraph.push(current.trim());
      i += 1;
    }
    blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
  }

  return blocks;
}

/** インデントの深さから入れ子を組み立てる */
function nest(raw: { indent: number; text: string }[]): ListItem[] {
  const items: ListItem[] = [];
  let index = 0;
  const base = raw.length > 0 ? raw[0].indent : 0;

  while (index < raw.length) {
    const current = raw[index];
    index += 1;
    const nested: string[] = [];
    while (index < raw.length && raw[index].indent > base) {
      nested.push(" ".repeat(raw[index].indent) + "- " + raw[index].text);
      index += 1;
    }
    items.push({
      text: current.text,
      children: nested.length > 0 ? parseBlocks(nested) : [],
    });
  }
  return items;
}

function splitRow(line: string): string[] {
  return line
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "heading": {
      // h1/h2 を大きくしすぎない。チャットの吹き出しの中に置かれるため、
      // 本文との差が開きすぎると見出しだけが浮く
      const size = block.level <= 2 ? "text-base" : "text-[15px]";
      return (
        <p className={`${size} pt-1 font-semibold text-slate-900`}>
          <Inline text={block.text} />
        </p>
      );
    }
    case "paragraph":
      return (
        <p className="whitespace-pre-wrap">
          <Inline text={block.text} />
        </p>
      );
    case "code":
      return (
        <pre className="overflow-x-auto rounded-lg bg-slate-900 px-3 py-2.5 text-[13px] leading-6 text-slate-100">
          <code>{block.code}</code>
        </pre>
      );
    case "quote":
      return (
        <blockquote className="border-l-2 border-slate-300 pl-3 text-slate-600">
          <Inline text={block.lines.join("\n")} />
        </blockquote>
      );
    case "rule":
      return <hr className="border-slate-200" />;
    case "list":
      return <ListView ordered={block.ordered} items={block.items} />;
    case "table":
      // 表は幅が読めない。ページ全体を横スクロールさせないよう内側で流す
      return (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {block.header.map((cell, i) => (
                  <th
                    key={i}
                    className="border-b border-slate-300 px-2 py-1.5 text-left font-semibold text-slate-900"
                  >
                    <Inline text={cell} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} className="border-b border-slate-100 px-2 py-1.5 align-top">
                      <Inline text={cell} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

function ListView({ ordered, items }: { ordered: boolean; items: ListItem[] }) {
  return (
    <ol className="space-y-1.5">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2">
          <span
            className={
              "shrink-0 select-none text-slate-400 " +
              (ordered ? "tabular-nums" : "leading-7")
            }
          >
            {ordered ? `${i + 1}.` : "・"}
          </span>
          <div className="min-w-0 flex-1">
            <span className="whitespace-pre-wrap">
              <Inline text={item.text} />
            </span>
            {item.children.length > 0 && (
              <div className="mt-1.5 pl-1">
                <BlockList blocks={item.children} />
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

// `**強調**` を `*斜体*` より先に試す必要があるため、この順で並べている
const INLINE_PATTERN = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|~~[^~\n]+~~)/g;

/** 行内の装飾。対応しない記法は文字としてそのまま出す */
function Inline({ text }: { text: string }): ReactNode {
  return text.split(INLINE_PATTERN).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="font-semibold text-slate-900">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("~~") && part.endsWith("~~") && part.length > 4) {
      return (
        <span key={i} className="text-slate-400 line-through">
          {part.slice(2, -2)}
        </span>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code
          key={i}
          className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[13px] text-slate-800"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return (
        <em key={i} className="italic">
          {part.slice(1, -1)}
        </em>
      );
    }
    return <span key={i}>{part}</span>;
  });
}
