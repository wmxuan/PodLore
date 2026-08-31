"""M1 测试：数据库幂等 CRUD（episodes 重复导入不产生重复行）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.infra import db
from tests.conftest import run


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch):
    """每个测试独立的临时数据库。"""
    monkeypatch.setenv("PODLORE_DB", str(tmp_path / "test.db"))
    run(db.init_db())
    yield


@pytest.fixture()
def base_meta(fixture_html) -> dict:
    from app.infra.fetch_xyz import extract_episode_meta, extract_next_data

    return extract_episode_meta(extract_next_data(fixture_html), html=fixture_html)


class TestUpsertEpisode:
    def test_insert_then_update_idempotent(self, tmp_db, base_meta):
        eid = base_meta["eid"]
        id1 = run(db.upsert_episode(base_meta))
        row1 = run(db.get_episode(eid))
        assert row1["id"] == id1
        assert row1["title"] == base_meta["title"]

        # 重复导入：改标题再入库 → 不新增行，字段被更新
        base_meta["title"] = "更新后的标题"
        id2 = run(db.upsert_episode(base_meta))
        rows = run(db.list_episodes())
        assert id2 == id1
        assert len(rows) == 1
        assert rows[0]["title"] == "更新后的标题"

    def test_podcast_upserted_alongside(self, tmp_db, base_meta):
        run(db.upsert_episode(base_meta))
        pid = base_meta["podcast"]["pid"]
        import aiosqlite

        async def count_podcasts():
            async with aiosqlite.connect(db.db_path()) as conn:
                cur = await conn.execute("SELECT COUNT(*) FROM podcasts WHERE pid = ?", (pid,))
                return (await cur.fetchone())[0]

        assert run(count_podcasts()) == 1

    def test_missing_eid_raises(self, tmp_db):
        with pytest.raises(ValueError):
            run(db.upsert_episode({"title": "没有 eid"}))


class TestEpisodeQueries:
    def test_get_missing_returns_none(self, tmp_db):
        assert run(db.get_episode("f" * 24)) is None

    def test_update_transcript_status(self, tmp_db, base_meta):
        eid = base_meta["eid"]
        run(db.upsert_episode(base_meta))
        for status in ("processing", "done"):
            run(db.update_transcript_status(eid, status))
        row = run(db.get_episode(eid))
        assert row["transcript_status"] == "done"
        assert row["created_at"]  # 默认值存在
