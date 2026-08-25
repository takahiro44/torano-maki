"""FastAPIのエントリポイント。

ここにはルーターの登録だけを置く。
4人が並行して作業するため、このファイルの変更頻度を最小に保ち、
コンフリクトの発生源にしないことを優先する。
ロジックは api/ と services/ に書くこと。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, health, ingest, knowledge, roleplay, search, summaries
from app.services.embedding import warmup


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """起動時に埋め込みモデルを読み込んでおく。

    遅延読み込みのままだと最初の1リクエストだけ約23秒かかる。
    デモの最初の操作でそれが起きると困るため、起動時に済ませる。
    処理の中身は services 側にあり、ここでは呼ぶだけに留める。
    """
    # uvicorn は自分のロガーにしかハンドラを付けないため、アプリ側のログは
    # 出力先が無く捨てられる。ウォームアップ中は20秒以上応答が無いので、
    # ログが出ないと停止したように見える。
    # ルートにハンドラを用意しつつ、INFOを通すのは app 配下だけに限定する
    # （ライブラリのINFOまで出すとログが読めなくなるため）。
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(message)s")
    logging.getLogger("app").setLevel(logging.INFO)
    warmup()
    yield


app = FastAPI(
    title="torano-maki API",
    description="営業ナレッジの蓄積・探索",
    version="0.1.0",
    lifespan=lifespan,
)

# フロントエンド(Vite)は別ポートで動くため、開発中はCORSを開けておく必要がある
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(knowledge.router)
app.include_router(search.router)
app.include_router(ingest.router)
app.include_router(summaries.router)
app.include_router(roleplay.router)
app.include_router(chat.router)
