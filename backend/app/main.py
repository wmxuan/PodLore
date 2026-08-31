"""PodLore 后端入口。

M0：最小可运行骨架；M2 起挂转写路由，后续里程碑逐步扩充。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.editor_api import router as editor_router
from app.api.episodes import router as episodes_router
from app.api.process import router as process_router
from app.api.reader_api import router as reader_router
from app.api.search_api import search_router  # M6：/api/search & /admin/search/rebuild & /search/state
from app.infra import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表/迁移（幂等）；M6：尝试预建 FTS5 索引；
    embedding 不预加载（惰性，避免启动慢；评测 / admin rebuild 再手动触发）。
    """
    await db.init_db()
    # M6 FTS：幂等重建一次（book_paras 很小，几十本书 < 0.5s）
    try:
        await db.rebuild_fts_index()
    except Exception:
        pass
    yield
    from app.services import process_service, transcribe_service

    transcribe_service._executor.shutdown(wait=False)
    process_service._executor.shutdown(wait=False)


app = FastAPI(title="PodLore", version="0.1.0", description="把播客变成你的书", lifespan=lifespan)
app.include_router(episodes_router)
app.include_router(process_router)
app.include_router(editor_router)
app.include_router(search_router)   # M6 先于 reader_router，/api/search 被 search_router 先注册（避免同名冲突）
app.include_router(reader_router)


@app.get("/health")
async def health() -> dict:
    """健康检查。"""
    return {"status": "ok", "app": "PodLore", "version": "0.1.0"}
