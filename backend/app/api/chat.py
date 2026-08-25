"""AIチャットのエンドポイント。

`/search` と分けているのは責務が違うため。`/search` は「検索結果を返す」
部品であり、`/chat` はそれを **Tool として使う** 側にある。
同じルーターに混ぜると依存の向きが読めなくなり、担当も分けられない。

ロジックは services/agent_loop.py にある。ここは HTTP の入口と、
LLM 側の失敗をステータスコードへ振り分けることだけを行う。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.chat import ChatRequest, ChatResponse
from app.services.agent_loop import run_agent_loop
from app.services.agent_tools import AGENT_TOOL_DEFINITIONS
from app.services.llm_client import LlmNotConfiguredError, LlmRequestError

router = APIRouter(prefix="/chat", tags=["chat"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest, db: DbSession) -> ChatResponse:
    """蓄積ナレッジをもとに質問へ答える。

    **応答に10秒以上かかる。** Agent が検索・根拠取得を挟むため、
    1リクエストで vLLM と複数回やりとりする。
    クライアント側のタイムアウトは余裕を持たせること（180秒以上を推奨）。

    会話履歴はサーバに残らない。**毎回すべての履歴を送ること**
    （理由は models/chat.py）。
    """
    try:
        result = run_agent_loop(db, payload.messages, top_k=payload.top_k)
    except LlmNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatResponse(
        answer=result.answer,
        citations=result.citations,
        tool_trace=result.tool_trace,
        usage=result.usage,
    )


@router.get("/tools")
def list_tools() -> list[dict[str, Any]]:
    """Agent に公開している Tool の定義を返す。

    フロント担当が「AIが何をできるのか」をコードを読まずに確認できるようにする。
    Tool を増減したときの実際の姿がここに出るため、資料より先に真実になる。
    """
    return AGENT_TOOL_DEFINITIONS
