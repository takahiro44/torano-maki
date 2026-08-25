"""商談要約 API のテスト。vLLM は呼ばない。"""

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.knowledge import CallSummaryDraft
from app.models.tables import DataSourceTable, UtteranceSegmentTable


def test_発言が無い出典は400(client: TestClient, db: Session) -> None:
    source = DataSourceTable(source_type="audio", file_name="empty.wav")
    db.add(source)
    db.flush()
    res = client.post("/summaries/generate", json={"data_source_id": str(source.id)})
    assert res.status_code == 400


def test_存在しない出典は404(client: TestClient) -> None:
    missing = uuid4()
    res = client.post("/summaries/generate", json={"data_source_id": str(missing)})
    assert res.status_code == 404


def test_セグメントから要約を保存する(client: TestClient, db: Session) -> None:
    source = DataSourceTable(source_type="audio", file_name="demo.wav")
    db.add(source)
    db.flush()
    db.add(
        UtteranceSegmentTable(
            data_source_id=source.id,
            sequence_no=1,
            speaker="salesperson",
            start_sec=0.0,
            end_sec=1.2,
            content="本日は在庫の見える化についてご相談です。",
        )
    )
    db.flush()
    draft = CallSummaryDraft(
        summary="在庫の見える化を相談した。",
        customer_needs=["在庫実態を把握したい"],
        proposals=["段階導入"],
        decisions=[],
        next_actions=["現場ヒアリング"],
    )
    with patch("app.services.summary.generate_summary", return_value=draft):
        res = client.post("/summaries/generate", json={"data_source_id": str(source.id)})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["summary"] == "在庫の見える化を相談した。"
    assert body["customer_needs"] == ["在庫実態を把握したい"]
    assert body["data_source_id"] == str(source.id)
