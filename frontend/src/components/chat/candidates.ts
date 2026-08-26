/**
 * 検索の候補を画面から取り直す。
 *
 * **Agentが受け取ったのは上位5件だけ。** ただし探した範囲はもっと広く、
 * それが見えないと「たまたま5件出てきた」ようにしか見えない。
 * 同じ検索関数に同じ検索文を投げているので、上位はAgentが受け取ったものと
 * 一致する。参照した5件と、選ばれなかった候補を描き分けられる。
 *
 * **回答は待たせない。** ここでの検索はLLMを通らず、埋め込みと
 * pgvector の問い合わせだけで返るため、待ち時間には効かない。
 */

import { useEffect, useRef, useState } from "react";
import { searchKnowledge } from "../../api/client";
import type { SourceType } from "../../types/api";
import type { Turn } from "./useChat";

/**
 * 候補として取る件数。
 *
 * **Agentに渡す件数（useChat の SEARCH_TOP_K）とは別物。** 渡す件数を増やすと
 * プロンプトが伸びて回答が目に見えて遅くなる（実測で全体20秒→37秒）。
 * こちらはLLMを通らないので、待ち時間に効かない。
 */
export const CANDIDATE_TOP_K = 24;

/** Agentが一次検索に使うTool名。候補を出すのはこのToolのときだけ */
const SEARCH_TOOL = "search_knowledge";

/** 検索で当たった1件。Agentに渡ったかどうかは問わない */
export type Candidate = {
  id: string;
  title: string;
  /** コサイン類似度。ベクトル検索で拾えなかった場合は null */
  semanticScore: number | null;
  /** 語の一致で拾えた場合のみ。ハイブリッド検索のもう一方の経路 */
  lexicalScore: number | null;
  dataSourceId: string | null;
  sourceType: SourceType;
};

export type CandidatesByStep = Record<number, Candidate[]>;

export function useCandidates(turn: Turn | null): CandidatesByStep {
  const [probe, setProbe] = useState<{ turnId: string | null; byStep: CandidatesByStep }>({
    turnId: null,
    byStep: {},
  });
  // 同じToolに二重に投げないための記録。ターンをまたいでも増え続けないよう、
  // ターンIDを含めた鍵で持つ
  const requested = useRef(new Set<string>());

  useEffect(() => {
    if (!turn) return;
    for (const step of turn.steps) {
      const query = step.args?.query;
      if (step.tool !== SEARCH_TOOL || typeof query !== "string" || !query.trim()) continue;
      const key = `${turn.id}:${step.step}`;
      if (requested.current.has(key)) continue;
      requested.current.add(key);
      searchKnowledge(query, CANDIDATE_TOP_K)
        .then((rows) =>
          setProbe((prev) => ({
            turnId: turn.id,
            byStep: {
              ...(prev.turnId === turn.id ? prev.byStep : {}),
              [step.step]: rows.map((r) => ({
                id: r.id,
                title: r.title,
                semanticScore: r.semantic_score,
                lexicalScore: r.lexical_score,
                dataSourceId: r.data_source_id,
                sourceType: r.source_type,
              })),
            },
          })),
        )
        // 候補が出ないだけで、会話にも出典にも影響しない
        .catch(() => {});
    }
  }, [turn]);

  return probe.turnId === turn?.id ? probe.byStep : {};
}

/** その検索で何番目に出たか。順位はスコアより素直に読める */
export function rankOf(candidates: Candidate[], knowledgeId: string): number | null {
  const index = candidates.findIndex((c) => c.id === knowledgeId);
  return index < 0 ? null : index + 1;
}
