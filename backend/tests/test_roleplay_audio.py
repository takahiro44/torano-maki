"""ロープレの音声回答。

**確認したいのは3つ。**

1. ブラウザのマイク録音（拡張子の無い webm Blob）が通ること
2. 生の練習音声が成功・失敗のどちらでもサーバに残らないこと
3. STTを回す前に、発言できる状態かを確かめていること

DGXへは接続しない。`transcribe()` は差し替える。
"""

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.roleplay import LearnerTurnRequest, RoleplaySessionCreate
from app.services.audio_upload import AudioUploadError, resolve_suffix, temporary_audio
from app.services.roleplay import add_learner_turn, create_feedback, create_session
from app.services.transcription import (
    EmptyTranscriptError,
    SttNotConfiguredError,
    SttRequestError,
    Transcript,
    TranscriptSegment,
)
from tests.test_roleplay_service import _FEEDBACK_JSON, _SCENARIO_JSON, _llm_body, _make_knowledge

_TRANSCRIPT = Transcript(
    text="なぜ他社より高いと感じられたのか教えていただけますか",
    segments=[
        TranscriptSegment(start=0.0, end=3.5, text="なぜ他社より高いと感じられたのか"),
        TranscriptSegment(start=3.5, end=5.2, text="教えていただけますか"),
    ],
    language="ja",
)


def _session_in_db(db: Session, *, max_turns: int = 2):
    knowledge = _make_knowledge(db)
    hits = [SimpleNamespace(id=knowledge.id)]
    with (
        patch("app.services.roleplay.search_knowledge", return_value=hits),
        patch("app.services.roleplay.chat_completion", return_value=_llm_body(_SCENARIO_JSON)),
    ):
        return create_session(db, RoleplaySessionCreate(query="値引き", max_turns=max_turns))


def _webm_blob() -> tuple[str, io.BytesIO, str]:
    """MediaRecorder が送ってくる形。ファイル名も拡張子も付かない。"""
    return ("blob", io.BytesIO(b"fake-webm-bytes"), "audio/webm;codecs=opus")


# ---------------------------------------------------------------------------
# 拡張子の判定
# ---------------------------------------------------------------------------


def test_拡張子が無くてもContent_Typeから判定する() -> None:
    # これが通らないとブラウザのマイク録音が一切送れない
    assert resolve_suffix("blob", "audio/webm;codecs=opus") == ".webm"
    assert resolve_suffix(None, "audio/wav") == ".wav"


def test_ファイル名の拡張子を優先する() -> None:
    assert resolve_suffix("answer.wav", "application/octet-stream") == ".wav"


def test_判定できない形式は拒否する() -> None:
    with pytest.raises(AudioUploadError) as exc:
        resolve_suffix("answer.txt", "text/plain")
    assert exc.value.code == "unsupported_format"


# ---------------------------------------------------------------------------
# 一時ファイルの後始末
# ---------------------------------------------------------------------------


def test_一時ファイルは正常終了で消える() -> None:
    with temporary_audio(io.BytesIO(b"audio"), suffix=".webm", max_bytes=1024) as path:
        assert path.exists()
        saved = path
    assert not saved.exists()


def test_一時ファイルは例外でも消える() -> None:
    # 生の練習音声をサーバに残さないことは仕様（計画書15章）
    saved: Path | None = None
    with pytest.raises(RuntimeError):
        with temporary_audio(io.BytesIO(b"audio"), suffix=".webm", max_bytes=1024) as path:
            saved = path
            raise RuntimeError("STTが落ちた")
    assert saved is not None
    assert not saved.exists()


def test_上限を超えた音声は書き切らずに拒否する() -> None:
    with pytest.raises(AudioUploadError) as exc:
        with temporary_audio(io.BytesIO(b"x" * 5000), suffix=".webm", max_bytes=1024):
            pass
    assert exc.value.code == "too_large"


def test_空の音声は拒否する() -> None:
    with pytest.raises(AudioUploadError) as exc:
        with temporary_audio(io.BytesIO(b""), suffix=".webm", max_bytes=1024):
            pass
    assert exc.value.code == "empty_file"


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


def test_マイク録音を文字起こしして返す(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    with patch("app.api.roleplay.transcribe", return_value=_TRANSCRIPT) as stt:
        response = client.post(
            f"/roleplay/sessions/{session.id}/turns/audio", files={"file": _webm_blob()}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == _TRANSCRIPT.text
    assert body["duration_sec"] == 5.2
    # 一時ファイルは .webm として渡っていること
    assert stt.call_args.args[0].suffix == ".webm"


def test_音声だけでは発言として保存されない(client: TestClient, db: Session) -> None:
    # 誤認識のまま顧客役が答えてしまわないよう、確認の段を残す（計画書8章）
    session = _session_in_db(db)
    with patch("app.api.roleplay.transcribe", return_value=_TRANSCRIPT):
        client.post(f"/roleplay/sessions/{session.id}/turns/audio", files={"file": _webm_blob()})

    view = client.get(f"/roleplay/sessions/{session.id}").json()
    assert view["learner_turns_used"] == 0
    assert len(view["turns"]) == 1


def test_発言回数を使い切っていればSTTを呼ばない(client: TestClient, db: Session) -> None:
    session = _session_in_db(db, max_turns=1)
    with patch(
        "app.services.roleplay.chat_completion", return_value=_llm_body({"content": "はい"})
    ):
        add_learner_turn(db, session, LearnerTurnRequest(content="1回目"))

    with patch("app.api.roleplay.transcribe") as stt:
        response = client.post(
            f"/roleplay/sessions/{session.id}/turns/audio", files={"file": _webm_blob()}
        )

    assert response.status_code == 409
    stt.assert_not_called()


def test_終了済みセッションではSTTを呼ばない(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    with patch(
        "app.services.roleplay.chat_completion", return_value=_llm_body({"content": "はい"})
    ):
        add_learner_turn(db, session, LearnerTurnRequest(content="回答"))
    with patch("app.services.roleplay.chat_completion", return_value=_llm_body(_FEEDBACK_JSON)):
        create_feedback(db, session)

    with patch("app.api.roleplay.transcribe") as stt:
        response = client.post(
            f"/roleplay/sessions/{session.id}/turns/audio", files={"file": _webm_blob()}
        )

    assert response.status_code == 409
    stt.assert_not_called()


def test_存在しないセッションへの音声は404(client: TestClient) -> None:
    from uuid import uuid4

    with patch("app.api.roleplay.transcribe") as stt:
        response = client.post(
            f"/roleplay/sessions/{uuid4()}/turns/audio", files={"file": _webm_blob()}
        )
    assert response.status_code == 404
    stt.assert_not_called()


def test_対応外の形式は400(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    response = client.post(
        f"/roleplay/sessions/{session.id}/turns/audio",
        files={"file": ("answer.txt", io.BytesIO(b"text"), "text/plain")},
    )
    assert response.status_code == 400


def test_STT未設定は503(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    with patch("app.api.roleplay.transcribe", side_effect=SttNotConfiguredError("未設定")):
        response = client.post(
            f"/roleplay/sessions/{session.id}/turns/audio", files={"file": _webm_blob()}
        )
    assert response.status_code == 503


def test_STTへ到達できなければ502(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    with patch("app.api.roleplay.transcribe", side_effect=SttRequestError("接続できません")):
        response = client.post(
            f"/roleplay/sessions/{session.id}/turns/audio", files={"file": _webm_blob()}
        )
    assert response.status_code == 502


def test_無音の音声はサーバ障害と区別して400(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    with patch("app.api.roleplay.transcribe", side_effect=EmptyTranscriptError("空でした")):
        response = client.post(
            f"/roleplay/sessions/{session.id}/turns/audio", files={"file": _webm_blob()}
        )
    assert response.status_code == 400


def test_STTが落ちても音声はサーバに残らない(client: TestClient, db: Session) -> None:
    session = _session_in_db(db)
    captured: list[Path] = []

    def _fail(path: Path, **_: object) -> Transcript:
        captured.append(path)
        raise SttRequestError("接続できません")

    with patch("app.api.roleplay.transcribe", side_effect=_fail):
        client.post(f"/roleplay/sessions/{session.id}/turns/audio", files={"file": _webm_blob()})

    assert captured
    assert not captured[0].exists()
