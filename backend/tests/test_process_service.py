"""M3 测试：process_service（mock LLM client → 4 项加工全链路）。

重点验证：金句溯源校验（找不到就丢弃）、广告保守策略（缺关键词绝不标记）、
摘要长度合规、大纲/金句/广告写入 DB。
"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path

import pytest

from app.infra import db, llm as llm_mod
from app.services import process_service as psvc
from tests.conftest import run

EID = "c" * 24

# 构造一段足够长、覆盖多种情况的「合成转写段落」：
# 段1-2：美妆话题 —— 金句候选 1
# 段3："感谢XX赞助" 广告词（唯一命中关键词的段落）
# 段4-5：正常商业讨论
SYN_PARAS = [
    {"seq": 1, "start_ts": 0.0,    "end_ts": 15.0,
     "text": "最近我们注意到美妆巨头集体盯上头发，洗护生意又热了起来。"
             "欧莱雅洗发护发产品销售额同比增长超过15%。"},
    {"seq": 2, "start_ts": 15.0,   "end_ts": 30.0,
     "text": "背后的核心趋势其实有三个：头皮护理专业化、男士护发崛起、功能性细分赛道爆发。"},
    {"seq": 3, "start_ts": 30.0,   "end_ts": 40.0,
     "text": "感谢本期由A品牌赞助，A品牌专注护肤多年，现在推出全新护发系列，点击链接购买哦。"},
    {"seq": 4, "start_ts": 40.0,   "end_ts": 55.0,
     "text": "雅诗兰黛等巨头今年都加大了护发投入，欧莱雅此前收购的美国护发品牌也已经官宣进入中国市场。"},
    {"seq": 5, "start_ts": 55.0,   "end_ts": 70.0,
     "text": "长期来看品牌方在洗护品类的布局还会持续加码，值得保持持续跟踪。"},
]


def _run_worker(monkeypatch) -> None:
    """把 LLM chat 换成一个可预测的 mock，模拟 4 轮任务返回（在模块内导入后直接替换）。"""

    # 先确保 service 已导入 chat 引用，然后 monkey-patch 模块级引用
    import app.services.process_service as svc_mod
    expected = [
        psvc.ChunkSummary(summary="欧莱雅洗护增长15%，美妆巨头关注头发，洗护赛道升温。"),
        psvc.FinalSummary(summary="本期节目讨论美妆巨头押注洗护赛道的趋势：欧莱雅增速15%以上，雅诗兰黛等品牌纷纷加码；三大驱动力分别是头皮护理专业化、男士护发崛起与功能细分。广告由A品牌赞助，节目围绕头发的投资机会展开。"),
        psvc.ChunkOutline(sections=[
            {"title": "背景：洗护升温", "start_ts": 0.0,  "end_ts": 30.0},
            {"title": "巨头布局",     "start_ts": 40.0, "end_ts": 70.0},
        ]),
        psvc.ChunkQuotes(quotes=[
            {"quote": "欧莱雅洗发护发产品销售额同比增长超过15%", "para_seq": 1,
             "reason": "硬数据，有说服力"},
            {"quote": "功能性细分赛道爆发", "para_seq": 2,
             "reason": "结构化总结"},
            {"quote": "长期来看品牌方在洗护品类的布局还会持续加码", "para_seq": 5,
             "reason": "观点型总结"},
            {"quote": "三大驱动力 头皮专业化 男士崛起 细分爆发", "para_seq": 2,
             "reason": "这段在原文找不到 → 应丢弃"},
        ]),
        psvc.FinalQuotes(quotes=[
            {"quote": "欧莱雅洗发护发产品销售额同比增长超过15%", "reason": "硬数据支撑"},
            {"quote": "功能性细分赛道爆发", "reason": "趋势归纳"},
            {"quote": "长期来看品牌方在洗护品类的布局还会持续加码", "reason": "前瞻性观点"},
            {"quote": "不存在的金句编造一条", "reason": "假的金句找不到就丢弃"},
        ]),
        # 广告：段3 明确含「感谢本期由」+「点击链接」。段4 没有关键词，绝不标
        psvc.AdVerdict(ads=[
            {"para_seq": 3, "reason": "感谢+赞助+点击链接"},
            {"para_seq": 4, "reason": "内容可能是合作推广"},  # 缺关键词 → 保守策略会被过滤
        ]),
    ]
    it = iter(expected)

    def fake(messages, json_schema=None, temperature=0.3, model=None):
        return next(it)

    monkeypatch.setattr(svc_mod, "chat", fake)
    psvc.start_process(EID).result(timeout=30)


@pytest.fixture()
def db_ready(tmp_path: Path, monkeypatch):
    """初始化临时库，写入 episode 与 5 段转写。"""
    monkeypatch.setenv("PODLORE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake")  # 只测 mock，不是真请求

    async def seed():
        await db.init_db()
        ep_id = await db.upsert_episode({
            "eid": EID, "pid": "p" * 24, "title": "合成测试：美妆巨头",
            "duration": 70, "podcast": {"pid": "p" * 24, "title": "节目", "author": "A",
                                       "brief": None, "cover_url": None},
        })
        await db.replace_transcript_paras(ep_id, [
            {"text": p["text"], "start": p["start_ts"], "end": p["end_ts"]}
            for p in SYN_PARAS
        ])
        await db.update_transcript_status(EID, "done")
        return await db.get_episode(EID)

    return run(seed())


class TestWorkerSuccess:
    def test_summary_length_and_persistence(self, db_ready, monkeypatch):
        _run_worker(monkeypatch)
        row = run(db.get_episode(EID))
        assert row["process_status"] == "done"
        assert row["process_progress"] == 1.0
        assert 0 < len(row["book_summary"]) <= 300

    def test_quotes_traceable(self, db_ready, monkeypatch):
        _run_worker(monkeypatch)
        quotes = run(db.get_quotes(db_ready["id"]))
        assert 3 <= len(quotes) <= 8
        for q in quotes:
            # 每一条金句都能在某段里找到（子串或模糊匹配）
            assert any(q["text"][:20] in p["text"] for p in SYN_PARAS), q

    def test_ad_conservative_never_marks_normal(self, db_ready, monkeypatch):
        _run_worker(monkeypatch)
        ads = run(db.get_ad_flags(db_ready["id"]))
        seqs = sorted(a["seq"] for a in ads)
        # 保守策略：段4（正常内容）不应被误杀，段3才是广告
        assert 4 not in seqs
        assert 3 in seqs

    def test_outline_timestamps_in_order(self, db_ready, monkeypatch):
        _run_worker(monkeypatch)
        outline = run(db.get_outline(db_ready["id"]))
        assert outline and all("title" in c and c["start_ts"] < c["end_ts"] for c in outline)
        starts = [c["start_ts"] for c in outline]
        assert all(a <= b for a, b in zip(starts, starts[1:]))


class TestWorkerGuard:
    def test_skip_when_no_transcript(self, db_ready, monkeypatch):
        run(db.update_transcript_status(EID, "pending"))
        psvc._worker(EID)
        assert run(db.get_episode(EID))["process_status"] == "pending"  # 无状态变更

    def test_skip_when_processing(self, db_ready, monkeypatch):
        run(db.update_process_status(EID, "processing"))
        psvc._worker(EID)  # 无 mock LLM → 防重入 early return
        assert run(db.get_episode(EID))["process_status"] == "processing"
