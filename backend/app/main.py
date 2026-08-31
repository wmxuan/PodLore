"""PodLore 后端入口。

M0：最小可运行骨架；M5 起挂业务路由。
"""

from fastapi import FastAPI

app = FastAPI(title="PodLore", version="0.1.0", description="把播客变成你的书")


@app.get("/health")
async def health() -> dict:
    """健康检查。"""
    return {"status": "ok", "app": "PodLore", "version": "0.1.0"}
