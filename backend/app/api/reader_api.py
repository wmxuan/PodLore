"""M5 阅读器 API：书全文 + 标注 CRUD + 搜索占位 + 标注列表。

前缀：/api（不含 /editor，与开发指令 §M5 接口名一致）。
注意：FastAPI 路由优先匹配先注册的同路径；但 episodes 路由 GET 仅占 /episodes/*/transcribe/transcript/process，
/books/* /annotations /search 未被占用，直接挂 /api 安全。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.infra import db
from app.services import book_service

router = APIRouter(prefix="/api")


class AnnotationCreate(BaseModel):
    book_para_id: int = Field(..., gt=0)
    offset_start: int = Field(..., ge=0)
    offset_end: int = Field(..., gt=0)
    color: str = Field("blue", pattern=r"^(blue|yellow|green|pink)$")
    note_text: str | None = None


# ---------- GET /api/books/{id} 全文 ----------

@router.get("/books/{book_id}")
async def api_get_book(book_id: int):
    """书全文（header + chapters.paras + episode.audio_url）。"""
    data = await book_service.get_book(book_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"book_id={book_id} 不存在")
    # 关联 episode 的 audio_url（播放器同步用）
    if data.get("episode_id"):
        ep = await db.get_episode_by_id(int(data["episode_id"]))
        if ep:
            data["audio_url"] = ep.get("audio_url") or ep.get("audio_path") or ""
            data["episode_eid"] = ep.get("eid") or ""
    # annotations 摘要（段内偏移，供阅读器按段着色）
    anns = await db.list_annotations_by_book(book_id)
    data["annotations"] = anns
    return data


# ---------- Annotations ----------

@router.post("/books/{book_id}/annotations", status_code=201)
async def api_create_annotation(book_id: int, body: AnnotationCreate):
    """创建标注。越界/跨书/段落不存在 → 400。"""
    try:
        aid = await db.insert_annotation(
            book_id=book_id,
            book_para_id=body.book_para_id,
            offset_start=body.offset_start,
            offset_end=body.offset_end,
            color=body.color,
            note_text=body.note_text,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    row = None
    for a in await db.list_annotations_by_book(book_id):
        if a["id"] == aid:
            row = a
            break
    return {"id": aid, **(row or {})}


@router.delete("/annotations/{ann_id}", status_code=200)
async def api_delete_annotation(ann_id: int):
    ok = await db.delete_annotation(ann_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"annotation {ann_id} 不存在")
    return {"status": "ok", "id": ann_id}


@router.get("/annotations")
async def api_list_annotations(book_id: int | None = Query(None)):
    """全部标注（按书聚合 UI 自己处理）；可选按 ?book_id=... 过滤。"""
    if book_id is not None:
        rows = await db.list_annotations_by_book(book_id)
    else:
        rows = await db.list_all_annotations()
    return {"count": len(rows), "rows": rows}


# ---------- GET /api/search（M5→M6 迁移） ----------
# 此端点已由 M6 `backend/app/api/search_api.py` 的 `/api/search` 取代（hybrid 向量+FTS）。
# 保留仅作向后兼容：把旧参数转调 search_api.search（import 延迟避免循环）。
# 返回 schema 也同步升级为 M6 结构（results / engine / embedding_ready 等）。

@router.get("/search")
async def api_search(q: str = Query(..., min_length=1),
                     top_k: int = Query(10, ge=1, le=50)):
    """M5→M6 向后兼容：直接转发给 search_api 的 hybrid 搜索。"""
    from fastapi import Query as _Q  # noqa: F401
    import sys as _sys
    mod = _sys.modules.get("app.api.search_api")
    if mod is None:
        # fallback：走 M5 LIKE
        rows = await db.search_book_paras(q, top_k=top_k)
        return {
            "q": q, "engine": "like_only_fallback",
            "embedding_ready": False, "embedding_error": None,
            "total": len(rows), "results": rows,
        }
    return await mod.search(q=q, top_k=top_k, engine="hybrid", include_context=True)
