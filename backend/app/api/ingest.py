"""ナレッジ蓄積のエンドポイント。担当: CLAUDE.md 1.1 を参照。

音声処理は時間がかかるため、HTTPリクエスト内で完結させない。
ジョブIDを返して非同期で処理する（CLAUDE.md 6章）。
"""

from fastapi import APIRouter

router = APIRouter(prefix="/ingest", tags=["ingest"])

# TODO: 以下を実装する
#   POST /ingest/text   テキストから構造化ナレッジを生成
#   POST /ingest/audio  音声をアップロードし、ジョブIDを返す
#   GET  /ingest/jobs/{job_id}  ジョブの進捗を返す
