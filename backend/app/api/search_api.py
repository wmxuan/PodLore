"""M6 语义搜索 API：向量为主 + FTS5 兜底。保持 M5 `GET /api/search` 字段契约。

路由覆盖关系：
- 之前 M5 reader_api.py 注册过 /search（LIKE）。
- 本模块 search_router 用 `prefix="/api"`，但先注册于 main 时会被先 match 吗？
  - 实际上 FastAPI 按注册顺序匹配；**为避免冲突**，本文件在 reader_api 内不包含
    `/api/search`（我们先在本模块 register search route，随后让 main 先 include
    search_router，再 include reader_router——FastAPI 先注册先匹配；但 search_router
    的 /search 与 reader_api 的 /search 同名——所以我们在 **main.py 中**不再让
    reader_router 注册的 GET /search 生效：reader_api 的 search 端点我们在 M6 先
    注释掉。
- 返回 schema 兼容 M5：
  [{"para_id","book_id","chapter_id","para_seq","para_text","start_ts","end_ts",
    "chapter_title","chapter_seq","book_title","cover_url","score","engine_hit"}, ...]
  额外顶层字段：
    engine='hybrid_vector_fts' / 'fts_only' / 'like_only' / 'vector_only'
    embedding_ready: bool, embedding_error: str|null（前端可在 UI 上展示「语义未启用」提示）
    total: int
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..infra.db import fts_available, rebuild_fts_index, search_fts_paras, search_book_paras
from ..infra.embedding import embedder_state
from ..infra.vector_store import build_index, mark_dirty, vector_search


search_router = APIRouter(prefix="/api")


# ---------- 工具：上下文（前后段） ----------

async def _attach_context(hits: list[dict]) -> list[dict]:
    """对每个命中，填 context_before / context_after（同书同章节 ±1 段文本）。
    为避免每条都查 DB，批量取一次同章节所有段落 seq。
    """
    if not hits:
        return hits
    # 聚合 book_id + chapter_id
    from collections import defaultdict
    import aiosqlite
    from ..infra.db import db_path
    groups: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, h in enumerate(hits):
        key = (int(h.get("book_id") or 0), int(h.get("chapter_id") or 0))
        if key == (0, 0):
            continue
        groups[key].append(i)
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        for (book_id, chapter_id), idxs in groups.items():
            cur = await db.execute(
                "SELECT seq, text FROM book_paras "
                "WHERE book_id = ? AND chapter_id = ? ORDER BY seq",
                (book_id, chapter_id),
            )
            rows = [dict(r) for r in await cur.fetchall()]
            seq_to_text = {r["seq"]: r["text"] for r in rows}
            seqs_sorted = sorted(seq_to_text)
            seq_idx = {s: i for i, s in enumerate(seqs_sorted)}
            for i in idxs:
                s = hits[i].get("para_seq")
                if s not in seq_idx:
                    continue
                pos = seq_idx[s]
                if pos > 0:
                    hits[i]["context_before"] = seq_to_text[seqs_sorted[pos - 1]]
                if pos < len(seqs_sorted) - 1:
                    hits[i]["context_after"] = seq_to_text[seqs_sorted[pos + 1]]
    return hits


# ---------- 合并：vector + fts（去重 + 分数归一） ----------

def _merge_results(vec_hits: list[dict], fts_hits: list[dict], top_k: int) -> list[dict]:
    """混合召回：vector cosine ∈ [-1,1]，fts 没有相似度（用 rank 位置打分 1/(rank+1)）。
    合并后按 score 降序；同段 id 去重（vector 优先保留）。"""
    seen: set[int] = set()
    merged: list[dict] = []
    # 向量先放（通常语义命中优先）
    for h in vec_hits:
        pid = int(h.get("para_id") or 0)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        merged.append(h)
    # fts 兜底分数：1 - (r / N)，N 取 fts_hits.size，避免与向量分差过大
    fts_n = max(1, len(fts_hits))
    for r, h in enumerate(fts_hits):
        pid = int(h.get("para_id") or 0)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        score = 1.0 - (r / fts_n)             # fts 原始无分数 → 线性打分
        score = 0.25 + 0.45 * score          # 映射到 [0.25, 0.70]，让高分向量 > FTS
        item = dict(h)
        item["score"] = round(score, 4)
        item["engine_hit"] = "fts"
        merged.append(item)
    merged.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return merged[:top_k]


# ---------- 管理端点：force rebuild 向量 & FTS ----------

@search_router.post("/admin/search/rebuild", tags=["admin"])
async def admin_search_rebuild():
    """幂等全量重建：FTS + 向量索引。用于 M4 freeze 新书后、评测前手动触发。"""
    try:
        await rebuild_fts_index()
        v_idx = await build_index(force=True)
        mark_dirty.__wrapped__ if False else None   # no-op
        mark_dirty()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"rebuild failed: {e}")
    st = embedder_state()
    return {
        "ok": True,
        "embedding": {"ready": st.ready, "error": st.error, "dim": st.dim},
        "fts_available": await fts_available(),
        "vector_count": len(v_idx.meta) if v_idx else 0,
    }


@search_router.get("/search/state", tags=["search"])
async def get_search_state():
    """前端/评测脚本查询搜索系统状态（不执行任何搜索）。"""
    from ..infra.vector_store import _index
    st = embedder_state()
    return {
        "embedding": {"ready": st.ready, "error": st.error, "dim": st.dim},
        "fts_available": await fts_available(),
        "vector_index": {
            "loaded": _index is not None,
            "rows": len(_index.meta) if _index else 0,
            "built_at": _index.built_at if _index else 0,
        },
    }


# ---------- 主搜索端点 ----------

@search_router.get("/search", tags=["search"])
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    top_k: int = Query(10, ge=1, le=50),
    engine: str = Query("hybrid", pattern="^(hybrid|vector|fts|like)$"),
    include_context: bool = Query(True),
):
    q = q.strip()
    if not q:
        return {
            "q": q, "engine": engine, "total": 0,
            "embedding_ready": False, "embedding_error": None,
            "results": [], "hits": 0, "rows": [], "query": q,
        }
    st = embedder_state()
    embedding_ready, embedding_error = st.ready, st.error

    vec_hits: list[dict] = []
    fts_hits: list[dict] = []

    # ----- 分支 1：向量 -----
    want_vec = (engine in ("hybrid", "vector")) and embedding_ready
    if want_vec:
        try:
            vec_hits = await vector_search(q, top_k=top_k)
        except Exception:
            vec_hits = []

    # ----- 分支 2：FTS -----
    want_fts = engine in ("hybrid", "fts")
    fts_used = False
    if want_fts:
        res = await search_fts_paras(q, top_k=top_k * 2)
        if res is not None:
            fts_hits = res
            fts_used = True
        else:
            # FTS 不可用 → LIKE 做关键词兜底（M5 路径）
            like = await search_book_paras(q, top_k=top_k * 2)
            # 包装：给每条附一个线性 score + engine_hit
            for r, h in enumerate(like):
                score = 0.25 + 0.45 * (1.0 - (r / max(1, len(like))))
                h["score"] = round(score, 4)
                h["engine_hit"] = "like"
            fts_hits = like

    # 合并
    if engine == "vector":
        merged = vec_hits[:top_k]
        final_engine = "vector_only" if embedding_ready else "vector_unavailable_no_results"
    elif engine == "fts":
        merged = fts_hits[:top_k]
        final_engine = "fts_only" if fts_used else "like_fallback"
    elif engine == "like":
        like_hits = await search_book_paras(q, top_k=top_k)
        for r, h in enumerate(like_hits):
            h["score"] = round(1.0 - r / max(1, len(like_hits)), 4)
            h["engine_hit"] = "like"
        merged = like_hits
        final_engine = "like_only"
    else:  # hybrid
        # 向量不足（少于 top_k 且全部分数低） → 多用 FTS 补
        # 简单策略：只要没到 top_k 就拿 FTS 补到 top_k
        merged = _merge_results(vec_hits, fts_hits, top_k)
        final_engine = "hybrid_vector_fts" if embedding_ready else (
            "fts_only" if fts_used else "like_only_fallback"
        )

    if include_context:
        merged = await _attach_context(merged)

    return {
        "q": q,
        "engine": final_engine,
        "embedding_ready": embedding_ready,
        "embedding_error": embedding_error,
        "total": len(merged),
        "results": merged,
        # M5 向后兼容：reader_api 之前返回 {hits, rows}；前端 & test 用了这两个字段
        "hits": len(merged),
        "rows": merged,
        "query": q,  # 老字段别名
    }
