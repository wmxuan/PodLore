"""编辑页 + 成书接口（命名空间 /api/editor/...，避免与 episodes router 同路径冲突）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.infra import db
from app.services import book_service, transcribe_service

router = APIRouter(prefix="/api/editor", tags=["editor"])


# ---- 编辑数据（编辑页首屏 GET /api/episodes/{eid}/transcript，已有 M2 endpoint）
#
# 这里补独立前缀 /editor 路由：保持 editor_api.py 语义，两个 endpoint 同时工作。

class EditItem(BaseModel):
    para_seq: int
    action: str = Field(pattern="^(keep|replace|delete)$")
    new_text: str | None = Field(default=None, description="action=replace 时必填")


class CreateBookRequest(BaseModel):
    edits: list[EditItem] = Field(default_factory=list)


@router.get("/episodes/{eid}/transcript",
            summary="编辑页：取转写稿 + 广告标记 + 金句 + 大纲（与 episodes 独立命名空间）")
async def get_transcript_for_editor(eid: str):
    episode = await transcribe_service.get_episode_or_none(eid)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"单集不存在：{eid}")
    ep_id = episode["id"]
    paras = await db.get_transcript_paras(ep_id)
    ads = {a["seq"]: a for a in await db.get_ad_flags(ep_id)}
    quotes = await db.get_quotes(ep_id)
    outline = await db.get_outline(ep_id)
    return {
        "eid": eid,
        "title": episode["title"],
        "cover_url": episode["cover_url"],
        "duration": episode["duration"],
        "series_name": episode.get("series_name"),
        "transcript_status": episode["transcript_status"],
        "process_status": episode["process_status"],
        "summary": episode.get("book_summary"),
        "outline": outline,
        "quotes": quotes,
        "paragraphs": [
            {
                "seq": p["seq"],
                "text": p["text"],
                "start_ts": p["start_ts"],
                "end_ts": p["end_ts"],
                "is_ad": bool(p.get("is_ad")),
                "ad_reason": p.get("ad_reason"),
            }
            for p in paras
        ],
        "ad_paragraphs": [
            {"seq": ads[k]["seq"], "reason": ads[k].get("reason", "广告段")}
            for k in sorted(ads.keys())
        ],
    }


@router.post("/episodes/{eid}/book", status_code=201, summary="创建冻结快照（书）")
async def create_book_endpoint(eid: str, payload: CreateBookRequest) -> dict[str, Any]:
    try:
        edits = [e.model_dump(exclude_unset=True) for e in payload.edits]
        return await book_service.create_book(eid, edits)
    except book_service.BookValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/books", summary="书架列表（按创建时间倒序）")
async def list_books():
    return {"books": await book_service.list_books()}


@router.get("/books/{book_id}", summary="单书全文（章节+段落，给 M5 阅读器用）")
async def get_book(book_id: int):
    result = await book_service.get_book(book_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"book_id={book_id} 不存在")
    return result
