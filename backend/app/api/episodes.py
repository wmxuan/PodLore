"""单集转写 API：启动后台转写 + 查询转写进度/结果。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services import transcribe_service

router = APIRouter(prefix="/api/episodes", tags=["episodes"])


@router.post("/{eid}/transcribe", status_code=202)
async def start_transcribe(eid: str):
    """启动后台转写任务（立即返回，进度走 GET /transcript 轮询）。"""
    episode = await transcribe_service.get_episode_or_none(eid)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"单集不存在：{eid}")
    transcribe_service.start_transcribe(eid)
    return {"eid": eid, "status": "processing", "detail": "转写任务已提交"}


@router.get("/{eid}/transcript")
async def get_transcript(eid: str):
    """查询转写状态/进度/段落结果。"""
    result = await transcribe_service.get_transcript(eid)
    if result is None:
        raise HTTPException(status_code=404, detail=f"单集不存在：{eid}")
    return result
