"""AIチャットの音声入力（POST /chat/voice）。

**確認したいのは3つ。**

1. ブラウザのマイク録音（拡張子の無い webm Blob）が通ること
2. **`data_sources` に行を作らないこと** — このエンドポイントを
   `/ingest/audio/transcribe` と分けた理由そのもの。ここが崩れると
   質問するたびに出典一覧へ「質問だけの商談音声」が積まれる
3. DGX側の失敗が、利用者の操作で直せる問題と区別して返ること

DGXへは接続しない。`transcribe()` は差し替える。
"""

import io
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import DataSourceTable, UtteranceSegmentTable
from app.services.transcription import (
    EmptyTranscriptError,
    SttNotConfiguredError,
    SttRequestError,
    Transcript,
    TranscriptSegment,
)

_TRANSCRIPT = Transcript(
    text="値引きを求められたときはどう対応しましたか",
    segments=[
        TranscriptSegment(start=0.0, end=2.4, text="値引きを求められたときは"),
        TranscriptSegment(start=2.4, end=4.1, text="どう対応しましたか"),
    ],
    language="ja",
)


def _webm_blob() -> tuple[str, io.BytesIO, str]:
    """MediaRecorder が送ってくる形。ファイル名も拡張子も付かない。"""
    return ("blob", io.BytesIO(b"fake-webm-bytes"), "audio/webm;codecs=opus")


def _count(db: Session, table: type) -> int:
    return db.execute(select(func.count()).select_from(table)).scalar_one()


def test_マイク録音を文字起こしして返す(client: TestClient) -> None:
    with patch("app.api.chat.transcribe", return_value=_TRANSCRIPT):
        response = client.post("/chat/voice", files={"file": _webm_blob()})

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == _TRANSCRIPT.text
    assert body["language"] == "ja"
    # 最後のセグメントの終端が話した長さになる
    assert body["duration_sec"] == 4.1


def test_出典を作らない(client: TestClient, db: Session) -> None:
    # `/ingest/audio/transcribe` と分けている唯一の理由。
    # ここが崩れると質問するたびに出典一覧が汚れる
    before_sources = _count(db, DataSourceTable)
    before_segments = _count(db, UtteranceSegmentTable)

    with patch("app.api.chat.transcribe", return_value=_TRANSCRIPT):
        client.post("/chat/voice", files={"file": _webm_blob()})

    assert _count(db, DataSourceTable) == before_sources
    assert _count(db, UtteranceSegmentTable) == before_segments


def test_回答まで進めない(client: TestClient) -> None:
    # 文字起こしは入力欄に入るだけ。STTの誤認識のまま Agent を走らせない
    with (
        patch("app.api.chat.transcribe", return_value=_TRANSCRIPT),
        patch("app.api.chat.run_agent_loop") as agent,
    ):
        client.post("/chat/voice", files={"file": _webm_blob()})

    agent.assert_not_called()


def test_対応しない形式は400(client: TestClient) -> None:
    response = client.post(
        "/chat/voice", files={"file": ("note.txt", io.BytesIO(b"text"), "text/plain")}
    )
    assert response.status_code == 400


def test_空の音声は400(client: TestClient) -> None:
    response = client.post("/chat/voice", files={"file": ("blob", io.BytesIO(b""), "audio/webm")})
    assert response.status_code == 400


def test_STT未設定は503(client: TestClient) -> None:
    # 設定漏れ。DGXの障害（502）と混ぜると .env を見に行けない
    error = SttNotConfiguredError("STT_BASE_URL が未設定")
    with patch("app.api.chat.transcribe", side_effect=error):
        response = client.post("/chat/voice", files={"file": _webm_blob()})
    assert response.status_code == 503


def test_無音は400(client: TestClient) -> None:
    # 送られた音声の問題。サーバ障害ではないので DGX を疑わせない
    with patch("app.api.chat.transcribe", side_effect=EmptyTranscriptError("文字起こしが空です")):
        response = client.post("/chat/voice", files={"file": _webm_blob()})
    assert response.status_code == 400


def test_DGXに繋がらないと502(client: TestClient) -> None:
    with patch("app.api.chat.transcribe", side_effect=SttRequestError("接続できません")):
        response = client.post("/chat/voice", files={"file": _webm_blob()})
    assert response.status_code == 502
