"""M3 测试：加工 API 路由（202 立即返回 / 加工未完成 409 / 404）。"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.infra import db
from app.main import app
from tests.conftest import run

EID = "d" * 24


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PODLORE_DB", str(tmp_path / "t.db"))
    run(db.init_db())
    ep_id = run(db.upsert_episode({
        "eid": EID, "pid": "p" * 24, "title": "API 加工测试",
        "duration": 10, "podcast": {"pid": "p" * 24, "title": "节目", "author": "A",
                                    "brief": None, "cover_url": None},
    }))
    # 不造段落（我们只测 API 入口，不跑真实加工）
    yield TestClient(app)
    run(db.replace_transcript_paras(ep_id, []))  # 不实际使用


class TestProcessApi:
    def test_missing_episode_404(self, client):
        resp = client.post(f"/api/episodes/{'f' * 24}/process")
        assert resp.status_code == 404

    def test_process_before_transcribe_returns_409(self, client):
        resp = client.post(f"/api/episodes/{EID}/process")
        assert resp.status_code == 409
        assert "转写尚未完成" in resp.json()["detail"]

    def test_process_ok_returns_202(self, client, monkeypatch):
        run(db.update_transcript_status(EID, "done"))
        fut: Future = Future()
        fut.set_result(None)
        monkeypatch.setattr(
            "app.services.process_service.start_process",
            lambda eid: fut,
        )
        resp = client.post(f"/api/episodes/{EID}/process")
        assert resp.status_code == 202
        assert resp.json()["status"] == "processing"

    def test_get_process_result_structure(self, client, monkeypatch):
        run(db.update_transcript_status(EID, "done"))
        resp = client.get(f"/api/episodes/{EID}/process")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("process_status", "process_progress", "summary",
                    "outline", "quotes", "ads"):
            assert key in body
        assert body["process_status"] == "pending"

    def test_get_process_missing_404(self, client):
        assert client.get(f"/api/episodes/{'f' * 24}/process").status_code == 404
