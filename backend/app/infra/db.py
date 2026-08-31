"""SQLite 持久层：建表 + 幂等 CRUD（aiosqlite，全 async）。

表：podcasts / episodes / transcript_paras（DDL 见《第一期开发实施指令》M1）。
幂等约定：episodes.eid 唯一、podcasts.pid 唯一；重复导入更新字段不新增行。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import aiosqlite

DDL = [
    """
    CREATE TABLE IF NOT EXISTS podcasts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pid TEXT UNIQUE, title TEXT, author TEXT, brief TEXT, cover_url TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episodes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pid TEXT, eid TEXT UNIQUE, title TEXT, description TEXT,
      duration INTEGER, pub_date TEXT,
      audio_url TEXT, audio_path TEXT, cover_url TEXT,
      shownotes_html TEXT,
      play_count INTEGER, clap_count INTEGER, favorite_count INTEGER, comment_count INTEGER,
      series_name TEXT,
      transcript_status TEXT DEFAULT 'pending',
      transcript_progress REAL DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transcript_paras (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      episode_id INTEGER, seq INTEGER, text TEXT, start_ts REAL, end_ts REAL
    )
    """,
]

# episodes 表业务字段（upsert 更新范围；created_at/transcript_status 不被覆盖）
_EPISODE_FIELDS = (
    "pid", "eid", "title", "description", "duration", "pub_date",
    "audio_url", "audio_path", "cover_url", "shownotes_html",
    "play_count", "clap_count", "favorite_count", "comment_count", "series_name",
)
_PODCAST_FIELDS = ("pid", "title", "author", "brief", "cover_url")


def db_path() -> Path:
    """数据库文件路径，默认 data/podlore.db，可用 PODLORE_DB 覆盖（测试用）。"""
    return Path(os.environ.get("PODLORE_DB", "data/podlore.db"))


async def init_db() -> None:
    """执行建表 DDL（幂等）；对旧库补齐 M2 新增列（迁移）。"""
    async with aiosqlite.connect(db_path()) as db:
        for ddl in DDL:
            await db.execute(ddl)
        # 旧库迁移：episodes 增加转写进度列（已存在则忽略）
        try:
            await db.execute(
                "ALTER TABLE episodes ADD COLUMN transcript_progress REAL DEFAULT 0"
            )
        except aiosqlite.OperationalError:
            pass  # 列已存在
        await db.commit()


async def _upsert_row(db: aiosqlite.Connection, table: str, key_col: str,
                      fields: tuple[str, ...], meta: dict[str, Any]) -> int:
    """按唯一键幂等写入：存在则更新业务字段并返回已有 id，否则插入返回新 id。"""
    key = meta.get(key_col)
    if not key:
        raise ValueError(f"{table} 缺少唯一键 {key_col}，无法入库")
    row = await db.execute(f"SELECT id FROM {table} WHERE {key_col} = ?", (key,))
    existing = await row.fetchone()
    values = [meta.get(f) for f in fields]
    if existing:
        sets = ", ".join(f"{f} = ?" for f in fields)
        await db.execute(
            f"UPDATE {table} SET {sets} WHERE {key_col} = ?", [*values, key]
        )
        return int(existing[0])
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = await db.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({marks})", values
    )
    return int(cur.lastrowid)


async def upsert_episode(meta: dict[str, Any]) -> int:
    """按 eid 幂等插入/更新单集，并顺带幂等入库节目信息。返回 episodes.id。"""
    async with aiosqlite.connect(db_path()) as db:
        if meta.get("podcast"):
            await _upsert_row(db, "podcasts", "pid", _PODCAST_FIELDS, meta["podcast"])
        episode_id = await _upsert_row(db, "episodes", "eid", _EPISODE_FIELDS, meta)
        await db.commit()
    return episode_id


async def get_episode(eid: str) -> dict[str, Any] | None:
    """按 eid 查询单集，不存在返回 None。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute("SELECT * FROM episodes WHERE eid = ?", (eid,))
        found = await row.fetchone()
        return dict(found) if found else None


async def list_episodes() -> list[dict[str, Any]]:
    """全部单集（按创建时间倒序）。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute("SELECT * FROM episodes ORDER BY created_at DESC, id DESC")
        return [dict(r) for r in await row.fetchall()]


async def update_transcript_status(eid: str, status: str) -> None:
    """更新转写状态：pending / processing / done / failed。"""
    async with aiosqlite.connect(db_path()) as db:
        await db.execute(
            "UPDATE episodes SET transcript_status = ? WHERE eid = ?", (status, eid)
        )
        await db.commit()


async def update_transcript_progress(eid: str, progress: float) -> None:
    """更新转写进度（0-1，按已处理音频时长/总时长）。"""
    async with aiosqlite.connect(db_path()) as db:
        await db.execute(
            "UPDATE episodes SET transcript_progress = ? WHERE eid = ?",
            (round(min(progress, 1.0), 4), eid),
        )
        await db.commit()


async def update_audio_path(eid: str, audio_path: str) -> None:
    """记录下载后的音频本地路径。"""
    async with aiosqlite.connect(db_path()) as db:
        await db.execute(
            "UPDATE episodes SET audio_path = ? WHERE eid = ?", (audio_path, eid)
        )
        await db.commit()


async def replace_transcript_paras(episode_id: int, paras: list[dict]) -> None:
    """写入转写段落（先清旧再插，重转写幂等），seq 从 1 递增。"""
    async with aiosqlite.connect(db_path()) as db:
        await db.execute(
            "DELETE FROM transcript_paras WHERE episode_id = ?", (episode_id,)
        )
        rows = [
            (episode_id, i, p["text"], p["start"], p["end"])
            for i, p in enumerate(paras, start=1)
        ]
        await db.executemany(
            "INSERT INTO transcript_paras (episode_id, seq, text, start_ts, end_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()


async def get_transcript_paras(episode_id: int) -> list[dict]:
    """按 seq 顺序读取转写段落。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute(
            "SELECT seq, text, start_ts, end_ts FROM transcript_paras "
            "WHERE episode_id = ? ORDER BY seq",
            (episode_id,),
        )
        return [dict(r) for r in await row.fetchall()]
