/**
 * ランキング行の見た目の計算。RankingView と、待機中/検索結果の行リスト
 * （KnowledgeRows）の両方で同じ見た目にするため共有する。
 */

/**
 * 帯の長さ。
 *
 * **生の類似度をそのまま幅にしない。** 上位は 0.80〜0.83 に固まるため、
 * 0〜100% に写すと全部同じ長さに見えて、順位の差が消える。
 * その検索の中での最大・最小に合わせて引き伸ばす。
 */
export function barWidth(value: number | null, max: number, min: number): string {
  if (value === null) return "18%";
  const span = max - min;
  const ratio = span < 0.001 ? 1 : (value - min) / span;
  return `${Math.round(28 + ratio * 72)}%`;
}

/**
 * 札を出す位置。
 *
 * **押した行の右へ、行の高さで開く。** 画面の中央に出すと、どの行を押したのかが
 * 分からなくなる。下にはみ出す場合だけ上へずらす。
 */
export function cardPosition(row: DOMRect): { top: number; left: number } {
  const width = 400;
  const estimatedHeight = Math.min(window.innerHeight * 0.7, 480);
  const left = Math.min(row.right + 12, window.innerWidth - width - 12);
  const top = Math.min(Math.max(row.top - 24, 100), window.innerHeight - estimatedHeight - 16);
  return { top, left };
}
