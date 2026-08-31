"""M4 测试：editor API（GET transcript / POST book / GET books / GET book）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.infra import db
from app.main import app
from tests.conftest import run

EID = "f" * 24
PARAS = [
    {"seq": 1, "text": "开场第一段。", "start_ts": 0, "end_ts": 5},
    {"seq": 2, "text": "广告：本期由XX赞助点击链接。", "start_ts": 5, "end_ts": 15},  # seq=2 广告
    {"seq": 3, "text": "数据与分析段落。", "start_ts": 15, "end_ts": 25},
    {"seq": 4, "text": "总结段落。", "start_ts": 25, "end_ts": 35},
]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PODLORE_DB", str(tmp_path / "t.db"))
    run(db.init_db())
    ep_id = run(db.upsert_episode({
        "eid": EID, "pid": "p" * 24, "title": "API 成书测试", "duration": 35,
        "podcast": {"pid": "p" * 24, "title": "节目", "author": "A",
                    "brief": None, "cover_url": None},
    }))
    run(db.replace_transcript_paras(ep_id, [
        {"text": p["text"], "start": p["start_ts"], "end": p["end_ts"]} for p in PARAS
    ]))
    run(db.replace_ad_flags(ep_id, [
        {"seq": 2, "is_ad": True, "reason": "感谢+赞助"},
    ]))
    run(db.replace_outline(ep_id, [
        {"title": "正文", "start_ts": 0, "end_ts": 35},
    ]))
    yield TestClient(app)


class TestEditorApi:
    def test_get_transcript_structure(self, client):
        resp = client.get(f"/api/editor/episodes/{EID}/transcript")
        assert resp.status_code == 200
        body = resp.json()
        assert body["eid"] == EID and body["title"] == "API 成书测试"
        assert len(body["paragraphs"]) == 4
        # 广告段左联 is_ad=true + ad_paragraphs 聚合
        ad_seq_2 = next(p for p in body["paragraphs"] if p["seq"] == 2)
        assert ad_seq_2["is_ad"] is True
        assert [a["seq"] for a in body["ad_paragraphs"]] == [2]

    def test_get_transcript_missing(self, client):
        assert client.get(f"/api/editor/episodes/{'9'*24}/transcript").status_code == 404

    def test_post_book_ok_and_shelf(self, client):
        r1 = client.post(f"/api/editor/episodes/{EID}/book",
                         json={"edits": [{"para_seq": 2, "action": "delete"}]})
        assert r1.status_code == 201, r1.text
        assert r1.json()["version"] == 1
        assert r1.json()["para_count"] == 3  # 删除广告段后
        r2 = client.post(f"/api/editor/episodes/{EID}/book", json={"edits": []})
        assert r2.json()["version"] == 2 and r2.json()["para_count"] == 4

        shelf = client.get("/api/editor/books").json()["books"]
        assert [b["version"] for b in shelf] == [2, 1]

        book = client.get(f"/api/editor/books/{r1.json()['id']}")
        assert book.status_code == 200
        paras = [p for c in book.json()["chapters"] for p in c["paras"]]
        assert len(paras) == 3
        assert not any("广告" in p["text"] for p in paras)

    def test_post_book_bad_edit_400(self, client):
        r = client.post(f"/api/editor/episodes/{EID}/book",
                        json={"edits": [{"para_seq": 99, "action": "delete"}]})
        assert r.status_code == 400

    def test_get_book_missing(self, client):
        assert client.get("/api/editor/books/999999").status_code == 404
