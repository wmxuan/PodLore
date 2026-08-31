"""PodLore 后端入口。

M0：最小可运行骨架；M2 起挂转写路由，后续里程碑逐步扩充。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.episodes import router as episodes_router
from app.api.process import router as process_router
from app.infra import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表/迁移（幂等），关闭时释放转写+加工线程池。"""
    await db.init_db()
    yield
    from app.services import process_service, transcribe_service

    transcribe_service._executor.shutdown(wait=False)
    process_service._executor.shutdown(wait=False)


app = FastAPI(title="PodLore", version="0.1.0", description="把播客变成你的书", lifespan=lifespan)
app.include_router(episodes_router)
app.include_router(process_router)


@app.get("/health")
async def health() -> dict:
    """健康检查。"""
    return {"status": "ok", "app": "PodLore", "version": "0.1.0"}
