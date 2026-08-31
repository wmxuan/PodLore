"""M2 测试：转写 API（202 立即返回 / 状态查询 / 404）。"""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import Future

import pytest
from fastapi.testclient import TestClient

from app.infra import db
from app.main import app
from tests.conftest import run

EID = "b" * 24


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PODLORE_DB", str(tmp_path / "t.db"))
    run(db.init_db())
    run(db.upsert_episode({
        "eid": EID, "pid": "p" * 24, "title": "API 测试单集", "duration": 100,
        "audio_url": "https://media.example.com/x.m4a",
        "podcast": {"pid": "p" * 24, "title": "节目", "author": "作者",
                    "brief": None, "cover_url": None},
    }))
    yield TestClient(app)


class TestTranscribeApi:
    def test_start_returns_202_immediately(self, client, monkeypatch):
        # mock 任务提交：返回已完成 Future，但不真正跑转写
        fut: Future = Future()
        fut.set_result(None)
        called = {}
        monkeypatch.setattr(
            "app.api.episodes.transcribe_service.start_transcribe",
            lambda eid: called.setdefault("eid", eid) or fut,
        )
        resp = client.post(f"/api/episodes/{EID}/transcribe")
        assert resp.status_code == 202
        assert resp.json()["status"] == "processing"
        assert called["eid"] == EID

    def test_start_unknown_episode_404(self, client):
        resp = client.post("/api/episodes/" + "f" * 24 + "/transcribe")
        assert resp.status_code == 404

    def test_get_transcript(self, client):
        resp = client.get(f"/api/episodes/{EID}/transcript")
        assert resp.status_code == 200
        body = resp.json()
        assert body["eid"] == EID
        assert body["status"] == "pending"
        assert body["paras"] == []

    def test_get_transcript_unknown_404(self, client):
        assert client.get("/api/episodes/" + "f" * 24 + "/transcript").status_code == 404
