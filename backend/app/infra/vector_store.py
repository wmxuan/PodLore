"""M6 向量检索：千级段落够用，numpy + 余弦相似度（不用向量数据库）。

- build_index()：把 book_paras 全量向量化，保存 data/vectors/vectors.npy + meta.json
  元数据保存每条的 para_id/book_id/chapter_id/book_title/chapter_title/para_text，
  顺序与 npy 行严格对齐。
- search(query, top_k)：query 向量化（由 embedding.embed 抛出异常时，
  上层改走 FTS/LIKE 兜底）→ 余弦相似度 top_k。
- 增量：简单全量重建（rebuild_index）；新书 / 编辑 M4 生成新版本时，
  通过 mark_dirty 触发下次 search 前自动重建（若内存中 index 已存在）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiosqlite
import numpy as np

from .config import data_dir
from .db import db_path
from .embedding import embed, init_embedder, embedder_state


VECTOR_DIR_NAME = "vectors"
VECTORS_NPY = "vectors.npy"
META_JSON = "meta.json"
SOURCE_VERSION_FILE = "source.version"  # 保存 source（book_paras）hash + 行数，判断是否需要重build


def vectors_dir() -> Path:
    p = data_dir() / VECTOR_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class VectorIndex:
    matrix: np.ndarray                     # (N, d) float32; 已做 L2 归一化（方便 cosine = matmul）
    meta: list[dict]                       # 长度 N，与 matrix 行对齐
    dim: int = 512
    built_at: float = 0.0
    source_hash: str = ""
    dirty: bool = False                    # 若 M4 freeze 新书入库 → True；下次 search 前自动 rebuild


_index: Optional[VectorIndex] = None
_index_lock = threading.RLock()


def get_index() -> Optional[VectorIndex]:
    """仅读，拿当前内存 index；不触发加载。"""
    return _index


def mark_dirty() -> None:
    """每次 M4 freeze 成功后调一下；下次 search 触发全量 rebuild。"""
    with _index_lock:
        global _index
        if _index is not None:
            _index.dirty = True


# ---------- source fingerprint（决定是否 need rebuild） ----------

async def _collect_source() -> list[dict]:
    """返回需要向量化的段落列表（按 bp.id 稳定排序）。
    每条的 text 聚合『书标题 + 章节标题 + 段正文』（让 bge 能拿到上下文，
    避免只看正文丢主题）。
    """
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bp.id AS para_id, bp.book_id, bp.chapter_id, bp.seq AS para_seq, "
            "  bp.text AS para_text, bp.start_ts, bp.end_ts, "
            "  bc.title AS chapter_title, bc.seq AS chapter_seq, "
            "  b.title AS book_title, b.cover_url "
            "FROM book_paras bp "
            "JOIN books b ON b.id = bp.book_id "
            "JOIN book_chapters bc ON bc.id = bp.chapter_id "
            "ORDER BY bp.id"
        )
        rows = [dict(r) for r in await cur.fetchall()]
    return rows


def _source_signature(rows: list[dict]) -> tuple[str, int]:
    """用 para_id + 段正文 sha1（跨 session 稳定）+ 行数。"""
    h = hashlib.sha1()
    for r in rows:
        h.update(str(r["para_id"]).encode())
        h.update(b"\x00")
        h.update((r.get("para_text") or "").encode("utf-8"))
    return h.hexdigest(), len(rows)


# ---------- 持久化 / 加载 ----------

def _save_index(idx: VectorIndex) -> None:
    vd = vectors_dir()
    np.save(vd / VECTORS_NPY, idx.matrix)
    with (vd / META_JSON).open("w", encoding="utf-8") as f:
        json.dump({
            "dim": idx.dim,
            "built_at": idx.built_at,
            "source_hash": idx.source_hash,
            "rows": idx.meta,
        }, f, ensure_ascii=False)
    with (vd / SOURCE_VERSION_FILE).open("w", encoding="utf-8") as f:
        f.write(f"{idx.source_hash}\n{len(idx.meta)}\n")


def _load_index_from_disk() -> Optional[VectorIndex]:
    vd = vectors_dir()
    npy, meta_path, src = vd / VECTORS_NPY, vd / META_JSON, vd / SOURCE_VERSION_FILE
    if not (npy.exists() and meta_path.exists() and src.exists()):
        return None
    try:
        matrix = np.load(npy)
        data = json.load(meta_path.open("r", encoding="utf-8"))
    except Exception:
        return None
    if matrix.ndim != 2:
        return None
    return VectorIndex(
        matrix=np.asarray(matrix, dtype=np.float32),
        meta=data["rows"],
        dim=int(data.get("dim", matrix.shape[1])),
        built_at=float(data.get("built_at", 0)),
        source_hash=data.get("source_hash", ""),
        dirty=False,
    )


def _current_source_version() -> Optional[tuple[str, int]]:
    vd = vectors_dir()
    src = vd / SOURCE_VERSION_FILE
    if not src.exists():
        return None
    try:
        h, n, *_ = src.read_text(encoding="utf-8").strip().split("\n") + ["", ""]
        return (h, int(n or 0))
    except Exception:
        return None


# ---------- 主入口 ----------

async def build_index(force: bool = False) -> Optional[VectorIndex]:
    """同步执行：拿 book_paras → 向量化 → 存盘；若无 embedding 则返回 None。

    说明：向量化走 embedding.embed（同步），但收集 db 用 async；
    整体用 async 包装方便 API / CLI 调用一致。
    """
    global _index
    rows = await _collect_source()
    if not rows:
        return None
    sig, n = _source_signature(rows)
    with _index_lock:
        # 命中磁盘缓存且 source 未变 → 直接加载（省 embedding 成本）
        if not force and (_index is not None) and not _index.dirty and _index.source_hash == sig:
            return _index
        saved = _load_index_from_disk()
        disk_sig = _current_source_version()
        if (not force and saved is not None and disk_sig is not None
                and disk_sig[0] == sig and disk_sig[1] == len(rows)
                and saved.matrix.shape[0] == len(rows)):
            _index = saved
            return _index
        # 嵌入：前置 init_embedder（可报具体错误给上层）
        st = init_embedder()
        if not st.ready:
            return None  # 等上层改走关键词兜底
        # 正文：标题 + 章 + 段拼接（bge 中文对短段落检索效果友好）
        texts = [
            (f"{r.get('book_title') or ''}\n{r.get('chapter_title') or ''}\n"
             f"{r.get('para_text') or ''}").strip()
            for r in rows
        ]
        # 运行在 event loop：embed 是同步，几千条 1~3s。小步 run_in_executor 避免阻塞？
        # 千级够用，直接调用。
        loop = asyncio.get_event_loop()
        try:
            vecs = await loop.run_in_executor(None, embed, texts)
        except RuntimeError:
            return None
        vecs = np.asarray(vecs, dtype=np.float32)
        # L2 normalize（cosine = query @ X.T）
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms < 1e-12] = 1.0
        vecs = vecs / norms
        idx = VectorIndex(
            matrix=vecs,
            meta=[{
                "para_id": r.get("para_id"), "book_id": r.get("book_id"),
                "chapter_id": r.get("chapter_id"), "para_seq": r.get("para_seq"),
                "para_text": r.get("para_text"),
                "start_ts": r.get("start_ts"), "end_ts": r.get("end_ts"),
                "chapter_title": r.get("chapter_title"),
                "chapter_seq": r.get("chapter_seq"),
                "book_title": r.get("book_title"), "cover_url": r.get("cover_url"),
            } for r in rows],
            dim=vecs.shape[1],
            built_at=time.time(),
            source_hash=sig,
            dirty=False,
        )
        _save_index(idx)
        _index = idx
        return idx


# ---------- 查询 ----------

def search_in_memory(query_vec: np.ndarray, top_k: int = 10,
                     idx: Optional[VectorIndex] = None,
                     min_score: float = 0.2) -> list[dict]:
    """query_vec 已归一化的 (1, dim)；返回 top_k [{...meta, score}]，
    score 为余弦相似度 ∈ [-1, 1]；低分裁掉（min_score 默认 0.2，
    语义词命中一般 >0.35，<0.2 基本噪声）。"""
    index = idx if idx is not None else _index
    if index is None or index.matrix.size == 0:
        return []
    top_k = max(1, min(top_k, 50))
    q = np.asarray(query_vec, dtype=np.float32).reshape(1, -1)
    if q.shape[1] != index.matrix.shape[1]:
        return []
    # cosine = matmul（X 已归一化；q 也归一化过）
    sim = (q @ index.matrix.T)[0]          # (N,)
    if sim.size <= top_k:
        order = np.argsort(-sim)
    else:
        # argpartition + sort：比 argsort 稳定且略快
        kth = min(top_k, sim.size - 1)
        part = np.argpartition(-sim, kth)[:top_k]
        order = part[np.argsort(-sim[part])]
    out: list[dict] = []
    for i in order.tolist():
        s = float(sim[i])
        if s < min_score:
            break
        item = dict(index.meta[i])
        item["score"] = s
        item["engine_hit"] = "vector"
        out.append(item)
    return out


async def vector_search(query: str, top_k: int = 10,
                        auto_rebuild: bool = True) -> list[dict]:
    """对外搜索入口：
    1. 若 embedding 不可用 → 返回空列表（上层去走关键词）
    2. 若 index 不存在 / dirty / 磁盘未建 → build_index()
    3. 编码 query → search_in_memory

    注意：bge 中文用户查询需以「搜索：*」开头可选提升（可选，这里兼容两种：
    先不带前缀搜；若 top 分 <0.28 且没加前缀，再带前缀重试一次）。
    """
    st = embedder_state()
    if not st.ready:
        _ = init_embedder()
        st = embedder_state()
        if not st.ready:
            return []
    with _index_lock:
        idx = _index
        need_build = (idx is None or idx.dirty)
    if need_build and auto_rebuild:
        await build_index(force=False)
    with _index_lock:
        idx = _index
    if idx is None:
        return []
    # embedding.query（同步），放 executor 不阻塞 ASGI loop
    loop = asyncio.get_event_loop()
    def _encode(txt: str) -> np.ndarray:
        v = embed([txt])
        v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-12)
        return v
    try:
        qv = await loop.run_in_executor(None, _encode, query)
    except RuntimeError:
        return []
    hits = search_in_memory(qv, top_k=top_k)
    if hits:
        return hits
    # 再带 bge 检索前缀试一次
    try:
        prefixed = f"为这个句子生成表示以用于检索相关文章：{query}"
        qv2 = await loop.run_in_executor(None, _encode, prefixed)
    except RuntimeError:
        return []
    return search_in_memory(qv2, top_k=top_k)
