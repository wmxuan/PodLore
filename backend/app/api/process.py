"""AI 加工 API：启动后台加工 + 查询加工结果。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services import process_service, transcribe_service

router = APIRouter(prefix="/api/episodes", tags=["process"])


@router.post("/{eid}/process", status_code=202)
async def start_process(eid: str):
    """启动后台 AI 加工（立即返回 202，结果走 GET /{eid}/process 轮询）。"""
    episode = await transcribe_service.get_episode_or_none(eid)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"单集不存在：{eid}")
    if episode["transcript_status"] != "done":
        raise HTTPException(
            status_code=409,
            detail=f"转写尚未完成（status={episode['transcript_status']}），暂不能加工",
        )
    process_service.start_process(eid)
    return {"eid": eid, "status": "processing", "detail": "AI 加工任务已提交"}


@router.get("/{eid}/process")
async def get_process(eid: str):
    """查询加工进度与结果。"""
    result = await process_service.get_process_result(eid)
    if result is None:
        raise HTTPException(status_code=404, detail=f"单集不存在：{eid}")
    return result
