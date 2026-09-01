"""转写任务编排：状态流转 + 后台线程执行 + 进度更新 + 结果查询。

异步模型：转写是 CPU 重任务（RTF≈0.17，4h 音频约 41 分钟），用全局单线程池
串行执行（避免多任务互抢核），start_transcribe 立即返回 Future，不阻塞请求。
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from loguru import logger

from app.infra import asr, db, downloader
from app.infra.config import audio_dir

# 转写任务串行：CPU 已满载，多集并行只会更慢。
# 惰性创建/重建：lifespan shutdown 会关闭本池（见 app/main.py），关闭后若再有
# submit（如 API 测试触发 lifespan 后又跑 worker 测试）会抛
# "cannot schedule new futures after shutdown"。_get_executor 检测 _shutdown 后自愈，
# 兼顾生产重启与测试隔离（不再需要按 "85+15 单独" 拆分跑 pytest）。
_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None or _executor._shutdown:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcribe")
    return _executor


def _db(coro) -> Any:
    """在工作线程里执行 DB 协程（每次独立事件循环，任务频率低，开销可忽略）。"""
    return asyncio.run(coro)


async def get_episode_or_none(eid: str) -> dict | None:
    """API 层用：查询单集是否存在（返回 episodes 行或 None）。"""
    return await db.get_episode(eid)


def start_transcribe(eid: str) -> Future:
    """提交后台转写任务，立即返回 Future（不阻塞调用方）。"""
    return _get_executor().submit(_worker, eid)


def _worker(eid: str) -> None:
    """后台转写 worker：pending→processing→done/failed，进度按片更新。"""
    row = _db(db.get_episode(eid))
    if row is None:
        logger.warning(f"转写跳过：episodes 表无 {eid}")
        return
    if row["transcript_status"] == "processing":
        logger.info(f"转写防重入：{eid} 已在处理中，忽略本次请求")
        return

    _db(db.update_transcript_status(eid, "processing"))
    try:
        audio_path = _ensure_audio(row)
        t0 = time.time()

        def on_progress(processed: float, total: float) -> None:
            if total > 0:
                _db(db.update_transcript_progress(eid, processed / total))

        segments = asr.transcribe(audio_path, progress_cb=on_progress)
        paras = asr.segment_to_paras(segments)
        _db(db.replace_transcript_paras(row["id"], paras))
        _db(db.update_transcript_status(eid, "done"))
        _db(db.update_transcript_progress(eid, 1.0))
        logger.info(
            f"转写完成 {eid}：{len(segments)} 分段 / {len(paras)} 段落，"
            f"音频 {row['duration'] or 0}s，耗时 {time.time() - t0:.1f}s"
        )
    except Exception as e:  # noqa: BLE001 整集失败置 failed
        logger.exception(f"转写失败 {eid}：{e}")
        _db(db.update_transcript_status(eid, "failed"))


def _ensure_audio(row: dict) -> Path:
    """音频就绪：本地有效则用本地；缺失/无效则按 audio_url 下载。"""
    eid = row["eid"]
    if row["audio_path"]:
        path = Path(row["audio_path"])
        if downloader.validate_audio(path):
            return path
        logger.info(f"本地音频无效（{path}），重新下载")
    if not row["audio_url"]:
        raise ValueError(f"{eid} 无本地音频且无 audio_url，无法转写")
    dest = audio_dir() / f"{eid}.m4a"
    downloader.download_audio(row["audio_url"], dest)
    _db(db.update_audio_path(eid, str(dest)))
    return dest


async def get_transcript(eid: str) -> dict | None:
    """查询转写结果：{eid, status, progress, paras}；单集不存在返回 None。"""
    row = await db.get_episode(eid)
    if row is None:
        return None
    paras: list[dict] = []
    if row["transcript_status"] == "done":
        paras = await db.get_transcript_paras(row["id"])
    return {
        "eid": eid,
        "title": row["title"],
        "status": row["transcript_status"],
        "progress": row["transcript_progress"] or 0,
        "duration": row["duration"],
        "paras": paras,
    }
