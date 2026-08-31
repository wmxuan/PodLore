"""M5 reader_api / annotations / search 测试。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.infra import db
from app.main import app
from tests.conftest import run

EID = "r" * 24
EP = {
    "pid": "p" * 24, "title": "节目", "author": "A", "brief": None, "cover_url": None,
    "podcast": {"pid": "p" * 24, "title": "节目", "author": "A", "brief": None, "cover_url": None},
    "eid": EID, "title": "美妆巨头集体盯上头发",
    "description": "", "duration": 34, "pub_date": "",
    "audio_url": "http://audio/x.m4a", "audio_path": "",
    "cover_url": "http://cover/1.jpg", "shownotes_html": "",
    "play_count": 0, "clap_count": 0, "favorite_count": 0,
    "comment_count": 0, "series_name": "声动早咖啡",
}
PARAS = [
    {"seq": 1, "text": "大家好欢迎来到今天的节目。",          "start_ts": 0,   "end_ts": 5},
    {"seq": 2, "text": "今天聊聊洗护市场的最新变化。",        "start_ts": 5,   "end_ts": 14},
    {"seq": 3, "text": "美妆巨头集体盯上头发赛道。",           "start_ts": 14,  "end_ts": 24},
    {"seq": 4, "text": "欧莱雅、联合利华都在投研发费用。",     "start_ts": 24,  "end_ts": 34},
]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PODLORE_DB", str(tmp_path / "t.db"))
    run(db.init_db())
    ep_id = run(db.upsert_episode(EP))
    run(db.replace_transcript_paras(ep_id, [
        {"text": p["text"], "start": p["start_ts"], "end": p["end_ts"]} for p in PARAS
    ]))
    run(db.replace_outline(ep_id, [{"title": "全文", "start_ts": 0, "end_ts": 34}]))
    yield TestClient(app)


def _create_book(c: TestClient) -> int:
    r = c.post(f"/api/editor/episodes/{EID}/book", json={"edits": []})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _paras_of(c: TestClient, bid: int):
    data = c.get(f"/api/books/{bid}").json()
    return [p for ch in data["chapters"] for p in ch["paras"]]


def test_get_book_full_contains_audio_and_timestamps(client):
    bid = _create_book(client)
    r = client.get(f"/api/books/{bid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["audio_url"] == "http://audio/x.m4a"
    assert d["para_count"] == 4 and d["chapter_count"] == 1
    assert d["annotations"] == []
    paras = _paras_of(client, bid)
    assert paras[2]["start_ts"] == 14.0 and paras[2]["end_ts"] == 24.0


def test_annotation_crud_validation(client):
    bid = _create_book(client)
    paras = _paras_of(client, bid)
    pid3 = paras[2]["id"]

    # 正常创建
    r = client.post(f"/api/books/{bid}/annotations", json={
        "book_para_id": pid3,
        "offset_start": 0, "offset_end": 4,
        "color": "blue",
        "note_text": "重点笔记",
    })
    assert r.status_code == 201, r.text
    aid = int(r.json()["id"])
    # 再 GET 书里摘要也能看到
    anns = client.get(f"/api/books/{bid}").json()["annotations"]
    assert any(a["id"] == aid for a in anns)

    # 422：color 不匹配 pattern
    assert client.post(f"/api/books/{bid}/annotations", json={
        "book_para_id": pid3, "offset_start": 0, "offset_end": 4,
        "color": "rainbow",
    }).status_code == 422

    # 400：offset_end <= start
    assert client.post(f"/api/books/{bid}/annotations", json={
        "book_para_id": pid3, "offset_start": 4, "offset_end": 4,
    }).status_code == 400

    # 400：越界
    r = client.post(f"/api/books/{bid}/annotations", json={
        "book_para_id": pid3, "offset_start": 0, "offset_end": 9999,
    })
    assert r.status_code == 400 and "越界" in r.json()["detail"]

    # 400：段不存在
    assert client.post(f"/api/books/{bid}/annotations", json={
        "book_para_id": 999999, "offset_start": 0, "offset_end": 1,
    }).status_code == 400

    # 400：跨书（v2 用了 v1 段 id）
    bid2 = _create_book(client)
    r = client.post(f"/api/books/{bid2}/annotations", json={
        "book_para_id": pid3, "offset_start": 0, "offset_end": 4,
    })
    assert r.status_code == 400

    # list + delete
    rows = client.get("/api/annotations").json()["rows"]
    assert any(x["id"] == aid for x in rows)
    rows_bid = client.get(f"/api/annotations?book_id={bid}").json()
    assert rows_bid["count"] == 1
    rows_bid2 = client.get(f"/api/annotations?book_id={bid2}").json()
    assert rows_bid2["count"] == 0

    assert client.delete(f"/api/annotations/{aid}").status_code == 200
    assert client.delete(f"/api/annotations/{aid}").status_code == 404

    # 书缺省 404
    assert client.get("/api/books/9999999").status_code == 404


def test_search_like_escape_wildcards(client):
    bid = _create_book(client)
    bid2 = _create_book(client)  # v2

    # 正常关键词命中
    r = client.get("/api/search?q=美妆巨头")
    assert r.status_code == 200
    assert r.json()["hits"] >= 1
    assert all("美妆巨头" in x["para_text"] for x in r.json()["rows"])

    # 未命中
    assert client.get("/api/search?q=外星文明").json()["hits"] == 0

    # 构造一段含下划线与百分号（通配符字面），检验转义生效不被当成通配
    # 直接 raw db 插入
    async def _insert():
        # 新建 v3：段文本含 '版本_v1 100%'
        ep = await db.get_episode(EID)
        await db.replace_transcript_paras(ep["id"], [
            {"seq": 1, "text": "数据 版本_v1 100% 完成。",
             "start": 0, "end": 5},
        ])
    run(_insert())
    from app.services import book_service
    v3 = run(book_service.create_book(EID, edits=[]))
    # 用 "%"/"_" 字面搜索必须精确命中只有这 1 条
    r = client.get("/api/search", params={"q": "版本_v1 100%"})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert r.json()["hits"] == 1, f"期望精确命中 1 条，实际 {len(rows)}: {rows}"
    assert rows[0]["book_id"] == v3["id"]
