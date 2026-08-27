/**
 * 上司がレビューを開いた瞬間に読む一言を組み立てる。
 *
 * **上司が知りたいのは「どこを教えればいいか」だけである。** 会話ログを読み返して
 * それを自分で判別するのは、毎回同じ作業を人の頭にやらせることになる。
 * 「ここは分かっている / ここは分かっていない / ナレッジにも無い」の3つが
 * 最初の1行で分かれば、あとは答えを書くだけになる。
 *
 * **LLMを呼ばない。** 材料（理解度の自己申告・問いごとのDB照合結果）は
 * 保存済みの構造化データとして揃っている。そこから機械的に作れる文を
 * わざわざモデルに書かせると、待ち時間が増えるうえ内容を保証できない
 * （components/chat/actions.ts と同じ理由）。
 *
 * **判定をこのファイルだけに置く。** 札の色分け・ペットのセリフ・本文が
 * それぞれ独自に「何が分かっていないか」を数え始めると、同じ依頼に対して
 * 違うことを言う画面になる。
 */

import type { ChatReviewDetail, ReviewQuestion, UnderstoodPoint } from "../types/api";

export type Briefing = {
  /** 上司の画面に出す本文 */
  text: string;
  /**
   * ペットに喋らせる分割。
   *
   * 1要素が1つの吹き出しになる。本文をそのまま渡すと、64pxの子の隣に
   * 段落が浮かぶことになる（AgentPet の吹き出しは一言のための幅しかない）。
   */
  lines: string[];
};

/** 呼び名。認証が無いので名乗りは任意（backend の ReviewQuestion.asked_by） */
export function learnerLabel(detail: ChatReviewDetail): string {
  const name = detail.learner_name?.trim();
  return name ? `${name}さん` : "後輩さん";
}

export function understoodPoints(detail: ChatReviewDetail): UnderstoodPoint[] {
  return detail.understood_points.filter((p) => p.level === "understood");
}

/** 本人が「あやしい」「わかってない」と答えた論点。**上司が最も教えるべき層** */
export function shakyPoints(detail: ChatReviewDetail): UnderstoodPoint[] {
  return detail.understood_points.filter((p) => p.level !== "understood");
}

/** ナレッジDBにも無かった問い。上司にしか答えられない */
export function missingQuestions(detail: ChatReviewDetail): ReviewQuestion[] {
  return detail.knowledge_gaps.filter((q) => q.db_state === "missing");
}

/** ナレッジDBには有ったのに辿り着けなかった問い。答えを書く必要は無い */
export function reachableQuestions(detail: ChatReviewDetail): ReviewQuestion[] {
  return detail.knowledge_gaps.filter((q) => q.db_state === "found_but_unreachable");
}

/** 本人が自分の言葉で書いた問い。会話に現れないため、AIには出せなかったもの */
export function ownQuestions(detail: ChatReviewDetail): ReviewQuestion[] {
  return detail.knowledge_gaps.filter((q) => q.source === "learner");
}

/** 列挙は2件まで。3件目からは件数にする。読み上げられる長さを超えると誰も読まない */
function enumerate(items: string[], limit = 2): string {
  const head = items.slice(0, limit).map((t) => `「${shorten(t)}」`);
  const rest = items.length - head.length;
  return rest > 0 ? `${head.join("と")}ほか${rest}件` : head.join("と");
}

function shorten(text: string, limit = 24): string {
  const flat = text.split(/\s+/).join(" ");
  return flat.length <= limit ? flat : `${flat.slice(0, limit)}…`;
}

export function reviewBriefing(detail: ChatReviewDetail): Briefing {
  const who = learnerLabel(detail);
  const understood = understoodPoints(detail);
  const shaky = shakyPoints(detail);
  const missing = missingQuestions(detail);
  const reachable = reachableQuestions(detail);
  const own = ownQuestions(detail);

  const lines: string[] = [];

  if (understood.length > 0) {
    lines.push(`${who}は、${enumerate(understood.map((p) => p.point))}は理解できています。`);
  }
  // **本人の申告をそのまま伝える。** 「言えてはいるが根拠が自分の中に無い」は
  // 会話ログからは読み取れず、聞いたからこそ分かったことである
  if (shaky.length > 0) {
    lines.push(
      `ただ${enumerate(shaky.map((p) => p.point))}は、本人いわく自分の言葉では説明できないそうです。`,
    );
  }
  if (missing.length > 0) {
    lines.push(
      `${enumerate(missing.map((q) => q.question))}については分かっておらず、ナレッジとしても蓄積にありません。`,
    );
  }
  if (reachable.length > 0) {
    lines.push(
      `${enumerate(reachable.map((q) => q.question))}はナレッジに有りましたが、本人は辿り着けていません。`,
    );
  }
  if (own.length > 0) {
    lines.push(`${own.length}件は、本人が自分で「ここが分からない」と書いた質問です。`);
  }
  if (lines.length === 0) {
    // 聞き取り前に送られた古い依頼。無いものを在るように言わない
    lines.push(`${who}からの質問です。中身は会話ログを見てください。`);
  }

  // 上司が最初にやることを最後に置く。読み終わったところが次の行動になる
  if (missing.length > 0) {
    lines.push(`まずは${missing.length}件、答えを書いてあげてください。`);
  } else if (reachable.length > 0) {
    lines.push("答えを書かなくても、ナレッジの適用場面を直すだけで済むかもしれません。");
  }

  return { text: lines.join(""), lines };
}
