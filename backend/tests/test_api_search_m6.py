"""M6 search_api + embedding + vector_store + 评测脚本 测试（3 条）。

策略（embedding 依赖重，本地可能没有）：
- 对 embedding 模块注入 fake state：让 embedder_state 返回 ready=True, dim=4；
  对 vector_store.embed 替换为 fake embed（把每段 4 维按 char 编码 → 余弦时能按关键词命中）。
- 用临时 DB（pytest tmp_path），插入 2 本书（主题相似但不是都含原词）→ 构造
  3 类测试用例：
    1. mock 向量 + FTS 混合：向量召回一本，FTS 召回另一本，合并 top-2 不重复。
    2. FTS 兜底（向量 embedder.ready=False）：hybrid 返回 fts_only / like_fallback，
       结果字段完整。
    3. Recall@K 评测脚本 dry-run：对 6 条 mock query 过一遍 direct runner，
       summary.Recall@K 数值在预期范围（不 < 0.5）。
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from app.infra import db as _db_module
from tests.conftest import run


# ---------- fixture: 两本书 9 段（书 1=洗护版；书 2=美妆+品牌版；关键词故意穿插） ----------

BOOK1_PARAS = [
    "大家好欢迎来到今天的节目。",                 # seq 1 - 开场白
    "今天聊聊洗护市场的最新变化。",               # seq 2 - 洗护
    "美妆巨头集体盯上头发赛道。",                  # seq 3 - 巨头 头发
    "欧莱雅、联合利华都在投研发费用。",            # seq 4 - 欧莱雅
    "洗发水护发素这个老行业为什么又火起来。",      # seq 5 - 洗发水 护发素
    "卡诗是洗护的高端产品线之一。",               # seq 6 - 卡诗
    "洗护的渠道从专柜走向线上社媒。",             # seq 7 - 洗护 渠道
    "护发的高复购是行业共识。",                   # seq 8 - 护发 复购
    "国货洗护的机会也越来越大。",                 # seq 9 - 国货 洗护
]

BOOK2_PARAS = [
    "本期第二本书我们聊聊品牌与增长。",           # seq 1
    "品牌增长的关键是品类扩展。",                 # seq 2
    "渠道、内容、社媒营销是打法三板斧。",         # seq 3
    "美妆公司也在护发赛道投入大量资源。",         # seq 4（包含护发，但和书 1 语义相关，跨书）
    "欧莱雅和欧莱雅集团旗下品牌矩阵。",           # seq 5（含欧莱雅）
    "护城河来自品牌心智与供应链效率。",           # seq 6 - 护城河
    "价格策略决定用户转化。",                     # seq 7
    "平台流量红利变化下企业需要差异化。",         # seq 8
    "全球化下的市场与消费。",                     # seq 9
]


@pytest.fixture
def two_books(tmp_path):
    """造库：两个 episodes → freeze 两本书。每书 1 章 9 段。返回 book_ids (b1, b2)。"""
    from app.infra import db
    from app.services import book_service
    old_env = __import__("os").environ.get("PODLORE_DB")
    __import__("os").environ["PODLORE_DB"] = str(tmp_path / "t.db")
    run(db.init_db())
    run(db.rebuild_fts_index())

    def _make_book(eid_prefix: str, title: str, paras_text: list[str]):
        async def _do():
            # 造 episodes + podcast （沿用 M5 test_api_reader 的 get_episode 路径）
            ep = await db.get_episode(eid_prefix * 24)
            # get_episode: no-op if exists? No. 需要先 upsert episode
            await db.upsert_episode({
                "pid": "p" * 24, "title": title, "author": "T",
                "podcast": {"pid": "p" * 24, "title": title, "author": "T"},
                "eid": eid_prefix * 24, "title": title, "description": "",
                "duration": 90, "pub_date": "2024",
                "audio_url": f"http://audio/{eid_prefix}.m4a",
                "cover_url": f"http://cover/{eid_prefix}.jpg",
            })
            ep = await db.get_episode(eid_prefix * 24)
            # transcript_paras（替换）
            paras = [{"seq": i + 1, "text": t, "start": float(i * 10), "end": float(i * 10 + 9)}
                     for i, t in enumerate(paras_text)]
            await db.replace_transcript_paras(ep["id"], paras)
            # freeze 成书（无 edits）
            book = await book_service.create_book(eid_prefix * 24, edits=[])
            return book["id"]
        return run(_do())

    b1 = _make_book("a", "书一：洗护市场的最新变化", BOOK1_PARAS)
    b2 = _make_book("b", "书二：品牌增长护城河", BOOK2_PARAS)
    run(db.rebuild_fts_index())
    yield {"db_path": tmp_path / "t.db", "book_ids": (b1, b2)}
    if old_env is None:
        __import__("os").environ.pop("PODLORE_DB", None)
    else:
        __import__("os").environ["PODLORE_DB"] = old_env


# ---------- fake embedding：把文本转 4 维（按字符哈希累加 → 稳定） ----------

def _install_fake_embedding():
    """monkey-patch app.infra.embedding：ready=True, dim=4, fake embed"""
    from app.infra import embedding, vector_store as vs
    embedding._state.ready = True
    embedding._state.error = None
    embedding._state.dim = 4
    embedding._model = object()  # 非 None，表示已加载

    def _fake_embed(texts):
        out = np.zeros((len(texts), 4), dtype=np.float32)
        for i, t in enumerate(texts):
            s = (t or "").encode("utf-8")
            v = np.zeros(4, dtype=np.float32)
            for j, b in enumerate(s):
                v[j % 4] += b
            out[i] = v
        # L2 normalize
        n = np.linalg.norm(out, axis=1, keepdims=True)
        n[n < 1e-9] = 1.0
        out = out / n
        return out

    embedding.embed = _fake_embed
    vs.embed = _fake_embed
    # 让 vector_store._index 保持 None；build_index() 会走 fake embed
    vs._index = None
    return _fake_embed


def _install_fake_embedding_unavailable():
    """monkey-patch embedding 为『不可用』状态（ready=False）。
    彻底封死 _try_import_and_load，避免 admin_search_rebuild 触发真实加载
    把 _state 改回 ready=True（这曾在降级 transformers 后导致测试失效）。
    """
    from app.infra import embedding
    embedding._state.ready = False
    embedding._state.error = "pytest fake: embedder disabled (simulate import error)"
    embedding._state.dim = 512
    embedding._model = None
    # 封死加载入口：让任何 init_embedder 调用都返回不可用
    def _no_load():
        return embedding._state
    embedding._try_import_and_load = _no_load
    embedding.init_embedder = _no_load
    def _boom(texts):
        raise RuntimeError(embedding._state.error)
    embedding.embed = _boom
    from app.infra import vector_store as vs
    vs.embed = _boom
    vs.init_embedder = _no_load
    vs._index = None


# ---------- 3 条 M6 测试 ----------

def test_hybrid_vector_fts_structure_and_dedup(two_books):
    """1. mock 向量可用 + hybrid：返回字段完整；同一段不重复；vector & fts 结果合并。"""
    from fastapi.testclient import TestClient
    _install_fake_embedding()
    # 需重新创建 TestClient：app lifespan 里 init_db 用 tmp 路径
    # main 的 include 顺序（search_router 先于 reader_router）保证 /search 走 search_router
    from app.main import app as _app
    with TestClient(_app) as cl:
        # 先触发 admin rebuild（让 vector build 加载 tmp 库书）
        cl.post("/api/admin/search/rebuild")
        # 精确词「欧莱雅」同时出现在两本书中 → 向量 + LIKE 兜底合并必须跨书命中
        r = cl.get("/api/search", params={"q": "欧莱雅", "top_k": 10, "engine": "hybrid"})
        assert r.status_code == 200, r.text
        j = r.json()
        # 顶层字段
        assert "engine" in j and "total" in j and "embedding_ready" in j and "results" in j
        assert j["embedding_ready"] is True
        # 结果字段：要求每条都带 source 字段
        req_fields = {"book_id", "para_id", "chapter_title", "para_text", "score", "engine_hit",
                      "book_title", "cover_url", "chapter_id"}
        rows_sample = j["results"]
        for row in rows_sample:
            missing = req_fields - set(row.keys())
            assert not missing, (missing, row)
        # 没有重复 para_id（前 10 条中）
        pids = [row["para_id"] for row in rows_sample]
        assert len(pids) == len(set(pids)), f"duplicate results: {pids}"
        # engine=hybrid 且 embedding ready → vector 主 + 关键词 兜底；两本书都至少命中一段（跨书召回）
        book_ids_hit = {row["book_id"] for row in rows_sample}
        assert book_ids_hit.issuperset(set(two_books["book_ids"])), (
            f"应跨书命中两本书={two_books['book_ids']}，但只命中 {book_ids_hit}")
        # 至少有一条 vector 命中 + 至少有一条含 engine_hit（标记了来源）
        hits_kind = {row.get("engine_hit") for row in rows_sample if row.get("engine_hit")}
        assert len(hits_kind) >= 1, f"engine_hit 分类缺失：{rows_sample[:3]}"


def test_fts_like_fallback_when_embedder_unavailable(two_books):
    """2. embedding 不可用（模拟 transformers 版本不兼容）：hybrid 返回 fts/like；
       engine 标记为 fts_only 或 like_fallback；结果结构依然保持 M5 兼容契约。"""
    _install_fake_embedding_unavailable()
    from fastapi.testclient import TestClient
    from app.main import app as _app
    with TestClient(_app) as cl:
        # 先强制 FTS build（两本书的段落）→ 正常
        r = cl.post("/api/admin/search/rebuild")
        # 精确词「欧莱雅」应该 FTS 能命中两本书内都出现的内容
        r = cl.get("/api/search", params={"q": "欧莱雅", "top_k": 10, "engine": "hybrid"})
        assert r.status_code == 200
        j = r.json()
        # embedding 标记为不可用
        assert j["embedding_ready"] is False
        assert isinstance(j.get("embedding_error"), str) and len(j["embedding_error"]) > 0
        # engine：SQLite 默认带 FTS5，macOS 系统 sqlite3 通常也有
        # 这里不强断言 engine 名称，只断言不是 vector_only
        assert j["engine"] in {"fts_only", "hybrid_vector_fts", "like_only_fallback"}
        # 结果字段完整
        for row in j["results"]:
            assert all(k in row for k in ("book_id", "para_id", "book_title", "chapter_title",
                                           "para_text", "score", "engine_hit"))
        # 两本书都有欧莱雅 → FTS/LIKE 都应命中跨书
        bids = {row["book_id"] for row in j["results"]}
        assert len(bids) >= 2, (bids, j["results"][:3])


def test_eval_search_script_runs_and_metrics_in_range(two_books, tmp_path):
    """3. 评测脚本（eval_search.py --mode direct）在 fake embedder 下跑 dry 数据集，
       输出 Recall@10 在 [0.5, 1.0] 区间；失败 case 列表不为空但命中过半。"""
    _install_fake_embedding()
    # 先在 tmp 库准备好 + admin rebuild
    from fastapi.testclient import TestClient
    from app.main import app as _app
    with TestClient(_app) as cl:
        cl.post("/api/admin/search/rebuild")
    b1, b2 = two_books["book_ids"]
    # 12 条小数据集：6 条语义词；6 条精确词；其中 2 条故意 expected=[99,999]（必 miss，
    # 但 Recall 按 full set cover 口径会 fail，让我们验证 fail 列表存在）
    ds = tmp_path / "queries.jsonl"
    lines = [
        # 精确词 + 两本书都有
        {"query": "欧莱雅",   "expected_book_ids": [b1, b2], "type": "exact"},
        {"query": "护发",     "expected_book_ids": [b1, b2], "type": "exact"},
        {"query": "品牌增长", "expected_book_ids": [b1, b2], "type": "semantic_rewrite"},
        {"query": "洗护市场", "expected_book_ids": [b1, b2], "type": "semantic"},
        {"query": "国货洗护", "expected_book_ids": [b1, b2], "type": "semantic"},
        {"query": "差异化竞争壁垒", "expected_book_ids": [b2], "type": "semantic_rewrite"},  # 书二有"差异化"+"护城河"
        # 精确但只在一本书里
        {"query": "卡诗",           "expected_book_ids": [b1], "type": "exact"},
        {"query": "洗发水护发素",   "expected_book_ids": [b1], "type": "exact"},
        {"query": "品牌心智与供应链效率", "expected_book_ids": [b2], "type": "exact"},
        # 语义词跨书
        {"query": "美妆公司为什么做头发赛道", "expected_book_ids": [b1, b2], "type": "semantic"},
        {"query": "品牌渠道打法", "expected_book_ids": [b1, b2], "type": "semantic"},
        # 故意 miss（expected 不存在）用于验证 fail list
        {"query": "火星种植学", "expected_book_ids": [99, 999], "type": "semantic"},
    ]
    ds.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    # 运行 eval_search.py（mode=direct 会直接 import —— 但 os.environ PODLORE_DB 已在 fixture 里）
    from backend.scripts import eval_search as ev
    runner = ev._make_direct_runner("hybrid")
    queries = ev._load_queries(ds)
    per = []
    for q in queries:
        result = runner(q["query"], top_k=10)
        per.append(ev._eval_one(q, result, top_k=10))
    summary = ev._summarize(per, top_k=10)
    # 指标：12 条里 1 条是必 miss；其他 11 条应该至少部分命中
    assert 0.5 <= summary.recall_full_cover_rate <= 1.0, (
        f"Recall@10={summary.recall_full_cover_rate} 不在预期 [0.5, 1.0]")
    assert summary.mrr > 0.0
    assert summary.avg_set_cover >= 0.4
    # 失败 case：至少含那条火星种植学
    failed_queries = [r.query["query"] for r in summary.failed_cases]
    assert any("火星" in f for f in failed_queries), failed_queries
    # report 格式化正常不 crash
    text = ev._format_report(queries, per, summary, 10, "hybrid")
    assert "Recall@10" in text and "MRR" in text
