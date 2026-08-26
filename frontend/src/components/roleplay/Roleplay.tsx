/**
 * ロープレの入口。URLに応じて開始画面・練習画面・振り返りを出し分ける。
 *
 * **セッションIDをURLに持たせている。** 練習は数分続くため、再読込や
 * デモ中の事故から復帰できる必要がある。バックエンドの
 * `GET /roleplay/sessions/{id}` はそのために用意した。
 *
 * 状態を親（App）へ持ち上げていないのは、ロープレを開いていない間に
 * 練習の状態を保つ必要が無いため。チャットと違い、URLから復元できる。
 *
 * **「今どの画面か」は state ではなくURLから導く。** 両方で持つと、
 * 戻る・進む・再読込のどれかで必ず食い違う。手元のセッションは
 * 「URLのIDと一致するときだけ有効」として扱う。
 */

import { useCallback, useEffect, useState } from "react";
import { getRoleplaySession, retryRoleplay } from "../../api/client";
import {
  ROLEPLAY_PATH,
  matchRoleplaySession,
  navigate,
  roleplaySessionPath,
  useRoutePath,
} from "../../lib/router";
import type { RoleplaySession } from "../../types/api";
import { RoleplayFeedbackView } from "./RoleplayFeedback";
import { RoleplaySessionView } from "./RoleplaySession";
import { RoleplayStart } from "./RoleplayStart";

type Props = {
  /** AIチャットの「この場面を練習する」から渡る。指定時はそのナレッジが主役になる */
  knowledgeId?: string;
  /** そのとき利用者が実際に打った疑問。場面をその問題意識に寄せるために使う */
  query?: string;
};

/** 失敗はセッションIDに紐づける。別の練習へ移ったときに古いエラーを出さないため */
type Failure = { sessionId: string; message: string };

export function Roleplay({ knowledgeId, query }: Props) {
  const path = useRoutePath();
  const routeSessionId = matchRoleplaySession(path);

  const [loaded, setLoaded] = useState<RoleplaySession | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [retrying, setRetrying] = useState(false);

  // URLのIDと一致するときだけ有効なセッションとして扱う
  const session = loaded !== null && loaded.session_id === routeSessionId ? loaded : null;
  const error = failure !== null && failure.sessionId === routeSessionId ? failure.message : null;

  // 再読込・戻る進む・「もう一度」のどれで来ても、同じ経路で復元される
  useEffect(() => {
    if (routeSessionId === null || session !== null || error !== null) return;

    let cancelled = false;
    getRoleplaySession(routeSessionId)
      .then((next) => {
        if (!cancelled) setLoaded(next);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const message = e instanceof Error ? e.message : String(e);
        setFailure({
          sessionId: routeSessionId,
          message: message.includes("見つかりません")
            ? "この練習は見つかりませんでした。新しく始めてください。"
            : message,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [routeSessionId, session, error]);

  const openSession = useCallback((next: RoleplaySession) => {
    setLoaded(next);
    // 開始直後に戻るボタンで「生成前のフォーム」へ戻っても意味が無いため置き換える
    navigate(roleplaySessionPath(next.session_id), { replace: true });
  }, []);

  async function retry() {
    if (session === null || retrying) return;
    setRetrying(true);
    try {
      const next = await retryRoleplay(session.session_id);
      setLoaded(next);
      // こちらは push。振り返りへ戻れると、前回との違いを見比べられる
      navigate(roleplaySessionPath(next.session_id));
    } catch (e) {
      setFailure({
        sessionId: session.session_id,
        message: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setRetrying(false);
    }
  }

  if (routeSessionId === null) {
    return (
      <RoleplayStart knowledgeId={knowledgeId} seedQuery={query} onStarted={openSession} />
    );
  }

  if (error !== null) {
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
        <button
          onClick={() => navigate(ROLEPLAY_PATH)}
          className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700
                     hover:border-slate-500"
        >
          場面を選び直す
        </button>
      </div>
    );
  }

  if (session === null) {
    return <p className="text-sm text-slate-500">練習を読み込んでいます…</p>;
  }

  if (session.feedback !== null) {
    return (
      <RoleplayFeedbackView
        feedback={session.feedback}
        references={session.references}
        onRetry={() => void retry()}
        onNewScene={() => navigate(ROLEPLAY_PATH)}
        retrying={retrying}
      />
    );
  }

  return <RoleplaySessionView session={session} onUpdated={setLoaded} />;
}
