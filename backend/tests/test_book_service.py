"""M4 测试：成书冻结快照（三表独立 + 重复建书不覆盖 + edits 语义 + 章节归属）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.infra import db
from app.services import book_service as bsvc
from tests.conftest import run

EID = "e" * 24

PARAS = [
    # 章节0：[0-30)
    {"seq": 1, "text": "大家好欢迎来到今天的节目。", "start_ts": 0.0,   "end_ts": 5.0},
    {"seq": 2, "text": "今天我们聊个轻松话题：护发。", "start_ts": 5.0,   "end_ts": 15.0},
    {"seq": 3, "text": "本期由A品牌赞助感谢点击链接购买。", "start_ts": 15.0, "end_ts": 30.0},  # 广告段
    # 章节1：[30-60)
    {"seq": 4, "text": "欧莱雅护发产品同比增长超过15%。", "start_ts": 30.0, "end_ts": 40.0},
    {"seq": 5, "text": "背后的三大驱动力其实很清晰。", "start_ts": 40.0, "end_ts": 50.0},
    {"seq": 6, "text": "分别是头皮专业化、男士护发与功能细分。", "start_ts": 50.0, "end_ts": 65.0},  # 略跨
    # 章节2：[65-90)
    {"seq": 7, "text": "长期来看品牌还会继续加码。", "start_ts": 70.0, "end_ts": 85.0},
]

OUTLINE = [
    {"seq": 1, "title": "开场",  "start_ts": 0.0,  "end_ts": 30.0},
    {"seq": 2, "title": "数据",  "start_ts": 30.0, "end_ts": 65.0},
    {"seq": 3, "title": "展望",  "start_ts": 65.0, "end_ts": 90.0},
]


@pytest.fixture()
def db_seeded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PODLORE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake")

    async def seed():
        await db.init_db()
        ep_id = await db.upsert_episode({
            "eid": EID, "pid": "p" * 24, "title": "M4 成书测试集",
            "duration": 90, "cover_url": "https://example.com/cover.jpg",
            "podcast": {"pid": "p" * 24, "title": "节目", "author": "A",
                        "brief": None, "cover_url": None},
        })
        await db.replace_transcript_paras(ep_id, [
            {"text": p["text"], "start": p["start_ts"], "end": p["end_ts"]}
            for p in PARAS
        ])
        await db.replace_outline(ep_id, OUTLINE)
        return ep_id

    ep_id = run(seed())
    return ep_id


class TestApplyEdits:
    def test_no_edits_default_keep_all(self):
        kept = bsvc._apply_edits(PARAS, None)
        assert [p["seq"] for p in kept] == [1, 2, 3, 4, 5, 6, 7]

    def test_delete_and_replace(self):
        edits = [
            {"para_seq": 1, "action": "delete"},
            {"para_seq": 4, "action": "replace", "new_text": "欧莱雅增长数据改写为 16%"},
            {"para_seq": 3, "action": "delete"},
        ]
        kept = bsvc._apply_edits(PARAS, edits)
        seqs = [p["seq"] for p in kept]
        assert 1 not in seqs and 3 not in seqs and 4 in seqs
        assert next(p for p in kept if p["seq"] == 4)["text"] == "欧莱雅增长数据改写为 16%"
        # 其他保持原顺序
        assert seqs == sorted(seqs)

    def test_validation_bad_seq_raises(self, db_seeded):
        with pytest.raises(bsvc.BookValidationError, match="不存在"):
            bsvc._apply_edits(PARAS, [{"para_seq": 999, "action": "delete"}])

    def test_validation_bad_action_raises(self):
        with pytest.raises(bsvc.BookValidationError, match="keep/replace/delete"):
            bsvc._apply_edits(PARAS, [{"para_seq": 1, "action": "x"}])

    def test_validation_replace_missing_text(self):
        with pytest.raises(bsvc.BookValidationError, match="new_text"):
            bsvc._apply_edits(PARAS, [{"para_seq": 1, "action": "replace"}])

    def test_empty_after_delete_raises(self, db_seeded):
        all_delete = [{"para_seq": p["seq"], "action": "delete"} for p in PARAS]
        with pytest.raises(bsvc.BookValidationError, match="无任何段落"):
            run(bsvc.create_book(EID, all_delete))


class TestChapterAssign:
    def test_basic_mapping(self):
        # 对 7 段，前三段应在章0（0-30）；中间 3 段（30-65）→ 章1；段7 [70,85] → 章2
        chapters, idxs = bsvc._assign_chapters(PARAS, OUTLINE)
        assert [c["title"] for c in chapters] == ["开场", "数据", "展望"]
        assert idxs == [0, 0, 0, 1, 1, 1, 2]

    def test_no_outline_fallback_single_chapter(self):
        chapters, idxs = bsvc._assign_chapters(PARAS, [])
        assert len(chapters) == 1 and chapters[0]["title"] == "全文"
        assert idxs == [0] * len(PARAS)


class TestCreateBook:
    def test_default_plain_snapshot(self, db_seeded):
        book1 = run(bsvc.create_book(EID))
        assert book1["version"] == 1
        assert book1["para_count"] == 7
        assert book1["chapter_count"] == 3
        assert book1["title"] == "M4 成书测试集"
        assert "example.com/cover" in book1["cover_url"]

        # 重复建书 → 新 id + version 2（冻结快照不覆盖）
        book2 = run(bsvc.create_book(EID))
        assert book2["id"] != book1["id"]
        assert book2["version"] == 2

        # 书架列表：id DESC，新版本在前
        shelf = run(bsvc.list_books())
        assert [b["id"] for b in shelf] == [book2["id"], book1["id"]]
        assert [b["version"] for b in shelf] == [2, 1]

    def test_edit_delete_and_replace_applied_in_snapshot(self, db_seeded):
        edits = [
            {"para_seq": 3, "action": "delete"},
            {"para_seq": 4, "action": "replace", "new_text": "改写：欧莱雅护发同比增长 16%（人工修正）"},
        ]
        book = run(bsvc.create_book(EID, edits))
        full = run(bsvc.get_book(book["id"]))
        all_paras = [p for c in full["chapters"] for p in c["paras"]]
        texts = [p["text"] for p in all_paras]
        # 广告段 seq3 已删除
        assert not any("本期由A品牌赞助" in t for t in texts)
        # 段4 改写生效
        assert any("改写：欧莱雅护发同比增长 16%" in t for t in texts)
        assert len(all_paras) == 6  # 7-1

    def test_snapshot_frozen_after_transcript_changed(self, db_seeded):
        book = run(bsvc.create_book(EID))
        before = run(bsvc.get_book(book["id"]))
        # 修改 transcript_paras（模拟后续重转写）
        ep_row = run(db.get_episode(EID))
        run(db.replace_transcript_paras(ep_row["id"], [
            {"text": "已被重写的新转写第" + str(i) + "段", "start": float(i), "end": float(i + 1)}
            for i in range(3)
        ]))
        after = run(bsvc.get_book(book["id"]))
        # 冻结快照 → 内容不变
        before_paras = [p["text"] for c in before["chapters"] for p in c["paras"]]
        after_paras = [p["text"] for c in after["chapters"] for p in c["paras"]]
        assert before_paras == after_paras
        assert after_paras[0] == "大家好欢迎来到今天的节目。"

    def test_create_unknown_eid_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PODLORE_DB", str(tmp_path / "x.db"))
        run(db.init_db())
        with pytest.raises(bsvc.BookValidationError, match="不存在"):
            run(bsvc.create_book("a" * 24))

    def test_get_book_missing_returns_none(self, db_seeded):
        assert run(bsvc.get_book(999999)) is None

    def test_cover_reused_on_card(self, db_seeded):
        book = run(bsvc.create_book(EID))
        assert "cover_url" in book and book["cover_url"].startswith("http")
