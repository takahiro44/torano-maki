"""FastAPIのエントリポイント。

ここにはルーターの登録だけを置く。
4人が並行して作業するため、このファイルの変更頻度を最小に保ち、
コンフリクトの発生源にしないことを優先する。
ロジックは api/ と services/ に書くこと。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, ingest, roleplay, search

app = FastAPI(
    title="torano-maki API",
    description="営業ナレッジの蓄積・探索・ロープレ",
    version="0.1.0",
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
app.include_router(ingest.router)
app.include_router(search.router)
app.include_router(roleplay.router)
