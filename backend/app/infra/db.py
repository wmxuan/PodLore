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
      process_status TEXT DEFAULT 'pending',  -- pending/processing/done/failed
      process_progress REAL DEFAULT 0,
      book_summary TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transcript_paras (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      episode_id INTEGER, seq INTEGER, text TEXT, start_ts REAL, end_ts REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_quotes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      episode_id INTEGER, text TEXT, start_ts REAL, end_ts REAL, reason TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_outline (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      episode_id INTEGER, seq INTEGER, title TEXT, start_ts REAL, end_ts REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_para_flags (
      episode_id INTEGER,
      seq INTEGER,            -- 与 transcript_paras.seq 对齐
      is_ad INTEGER DEFAULT 0,
      ad_reason TEXT,
      PRIMARY KEY (episode_id, seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS books (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      episode_id INTEGER, title TEXT, cover_url TEXT,
      created_at TEXT DEFAULT (datetime('now')), version INTEGER DEFAULT 1,
      chapter_count INTEGER DEFAULT 0, para_count INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS book_chapters (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id INTEGER, seq INTEGER, title TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS book_paras (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id INTEGER, chapter_id INTEGER, seq INTEGER,
      text TEXT, start_ts REAL, end_ts REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS annotations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id INTEGER, book_para_id INTEGER,
      offset_start INTEGER, offset_end INTEGER,
      color TEXT DEFAULT 'blue', note_text TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
    """,
]

# episodes 表业务字段（upsert 更新范围）
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
    """执行建表 DDL（幂等）；对旧库补齐 M2/M3 新增列与新表（迁移）。"""
    async with aiosqlite.connect(db_path()) as db:
        for ddl in DDL:
            await db.execute(ddl)
        # M2 / M3 新增列迁移（列已存在则忽略）
        for col in [
            ("episodes", "transcript_progress REAL DEFAULT 0"),
            ("episodes", "process_status TEXT DEFAULT 'pending'"),
            ("episodes", "process_progress REAL DEFAULT 0"),
            ("episodes", "book_summary TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE {col[0]} ADD COLUMN {col[1]}")
            except aiosqlite.OperationalError:
                pass
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


async def get_episode_by_id(episode_id: int) -> dict[str, Any] | None:
    """按 episodes.id 查询（书 → episode 的关联）。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
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
    """按 seq 顺序读取转写段落（左联 para_flags，附带 is_ad / ad_reason）。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT p.seq, p.text, p.start_ts, p.end_ts, "
            "  COALESCE(f.is_ad, 0) AS is_ad, f.ad_reason "
            "FROM transcript_paras p "
            "LEFT JOIN episode_para_flags f "
            "  ON f.episode_id = p.episode_id AND f.seq = p.seq "
            "WHERE p.episode_id = ? ORDER BY p.seq",
            (episode_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def update_process_status(eid: str, status: str, progress: float | None = None) -> None:
    """更新 AI 加工状态：pending / processing / done / failed；可同时写进度。"""
    async with aiosqlite.connect(db_path()) as db:
        if progress is None:
            await db.execute(
                "UPDATE episodes SET process_status = ? WHERE eid = ?", (status, eid)
            )
        else:
            await db.execute(
                "UPDATE episodes SET process_status = ?, process_progress = ? WHERE eid = ?",
                (status, round(min(progress, 1.0), 4), eid),
            )
        await db.commit()


async def update_book_summary(episode_id: int, summary: str) -> None:
    """更新书摘要（300 字内）。"""
    async with aiosqlite.connect(db_path()) as db:
        await db.execute(
            "UPDATE episodes SET book_summary = ? WHERE id = ?", (summary, episode_id)
        )
        await db.commit()


async def replace_quotes(episode_id: int, quotes: list[dict]) -> None:
    """写入金句（幂等：先清后插）。"""
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("DELETE FROM episode_quotes WHERE episode_id = ?", (episode_id,))
        rows = [(episode_id, q["text"], q["start_ts"], q["end_ts"], q.get("reason", ""))
                for q in quotes]
        await db.executemany(
            "INSERT INTO episode_quotes (episode_id, text, start_ts, end_ts, reason) "
            "VALUES (?, ?, ?, ?, ?)", rows,
        )
        await db.commit()


async def get_quotes(episode_id: int) -> list[dict]:
    """按 seq（实际按 start_ts）顺序读取金句。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT text, start_ts, end_ts, reason FROM episode_quotes "
            "WHERE episode_id = ? ORDER BY start_ts", (episode_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def replace_outline(episode_id: int, outline: list[dict]) -> None:
    """写入大纲章节（幂等：先清后插，seq 从 1 起递增）。"""
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("DELETE FROM episode_outline WHERE episode_id = ?", (episode_id,))
        rows = [(episode_id, i, ch["title"], ch["start_ts"], ch["end_ts"])
                for i, ch in enumerate(outline, start=1)]
        await db.executemany(
            "INSERT INTO episode_outline (episode_id, seq, title, start_ts, end_ts) "
            "VALUES (?, ?, ?, ?, ?)", rows,
        )
        await db.commit()


async def get_outline(episode_id: int) -> list[dict]:
    """按 seq 顺序读取大纲。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT seq, title, start_ts, end_ts FROM episode_outline "
            "WHERE episode_id = ? ORDER BY seq", (episode_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def replace_ad_flags(episode_id: int, flags: list[dict]) -> None:
    """写入段落级广告标记（幂等：先清后插；只保留 is_ad=1 的行，减少存盘）。"""
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("DELETE FROM episode_para_flags WHERE episode_id = ?", (episode_id,))
        rows = [(episode_id, f["seq"], 1, f.get("reason", ""))
                for f in flags if f.get("is_ad")]
        if rows:
            await db.executemany(
                "INSERT INTO episode_para_flags (episode_id, seq, is_ad, ad_reason) "
                "VALUES (?, ?, ?, ?)", rows,
            )
        await db.commit()


async def get_ad_flags(episode_id: int) -> list[dict]:
    """返回 is_ad=1 的段落级广告标记。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT seq, is_ad, ad_reason AS reason FROM episode_para_flags "
            "WHERE episode_id = ? ORDER BY seq", (episode_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def insert_book(episode_id: int, title: str, cover_url: str | None,
                      chapter_count: int, para_count: int) -> int:
    """创建 book 行（version 取该集已有书的最大 version + 1），返回 book id。"""
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM books WHERE episode_id = ?",
            (episode_id,),
        )
        row = await cur.fetchone()
        version = int(row[0]) if row else 1
        cur = await db.execute(
            "INSERT INTO books (episode_id, title, cover_url, version, chapter_count, para_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (episode_id, title, cover_url or "", version, chapter_count, para_count),
        )
        await db.commit()
        return int(cur.lastrowid)


async def insert_book_chapters(book_id: int, chapters: list[dict]) -> list[int]:
    """批量插入章节，返回 chapter_id 列表（与 chapters 顺序一致）。"""
    async with aiosqlite.connect(db_path()) as db:
        ids: list[int] = []
        for i, ch in enumerate(chapters, start=1):
            cur = await db.execute(
                "INSERT INTO book_chapters (book_id, seq, title) VALUES (?, ?, ?)",
                (book_id, ch.get("seq", i), ch["title"]),
            )
            ids.append(int(cur.lastrowid))
        await db.commit()
        return ids


async def insert_book_paras(book_id: int, chapter_ids: list[int],
                            paras: list[dict]) -> None:
    """批量插入段落。每个 para 含 {chapter_idx, seq, text, start_ts, end_ts}，
    chapter_idx 对应 chapter_ids 的下标。"""
    async with aiosqlite.connect(db_path()) as db:
        rows = []
        seq_g = 1
        for p in paras:
            cid = chapter_ids[int(p["chapter_idx"])]
            rows.append((book_id, cid, seq_g, p["text"],
                         float(p["start_ts"]), float(p["end_ts"])))
            seq_g += 1
        await db.executemany(
            "INSERT INTO book_paras (book_id, chapter_id, seq, text, start_ts, end_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)", rows,
        )
        await db.commit()


async def list_books() -> list[dict]:
    """书架列表：按创建时间倒序。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, episode_id, title, cover_url, created_at, version, "
            "  chapter_count, para_count "
            "FROM books ORDER BY created_at DESC, id DESC"
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_book_header(book_id: int) -> dict | None:
    """返回 books 行 + chapter_count。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, episode_id, title, cover_url, created_at, version, "
            "  chapter_count, para_count FROM books WHERE id = ?", (book_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_book_chapters(book_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, seq, title FROM book_chapters WHERE book_id = ? ORDER BY seq", (book_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_book_paras(book_id: int) -> list[dict]:
    """按 chapter_id.seq 顺序返回段落。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bp.id, bp.chapter_id, bc.seq AS chapter_seq, "
            "  bp.seq, bp.text, bp.start_ts, bp.end_ts "
            "FROM book_paras bp "
            "JOIN book_chapters bc ON bc.id = bp.chapter_id "
            "WHERE bp.book_id = ? "
            "ORDER BY bc.seq, bp.seq", (book_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


# ---------- annotations ----------

async def insert_annotation(book_id: int, book_para_id: int,
                            offset_start: int, offset_end: int,
                            color: str = "blue",
                            note_text: str | None = None) -> int:
    """创建标注。range 越界则抛 ValueError（由调用方捕获转 400）。"""
    if offset_start < 0 or offset_end <= offset_start:
        raise ValueError(
            f"非法文本偏移：offset_start={offset_start} offset_end={offset_end}"
        )
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        # 锚定范围越界校验：段落必须属于该书
        row = await db.execute(
            "SELECT id, book_id, text FROM book_paras WHERE id = ?",
            (book_para_id,),
        )
        para = await row.fetchone()
        if para is None:
            raise ValueError(f"book_para_id={book_para_id} 不存在")
        if int(para["book_id"]) != book_id:
            raise ValueError(
                f"段落 {book_para_id} 不属于该书 {book_id}"
            )
        txt_len = len((para["text"] or ""))
        if offset_end > txt_len:
            raise ValueError(
                f"偏移越界：offset_end={offset_end} 段落长度={txt_len}"
            )
        cur = await db.execute(
            "INSERT INTO annotations "
            "(book_id, book_para_id, offset_start, offset_end, color, note_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (book_id, book_para_id, offset_start, offset_end,
             color, note_text or None),
        )
        await db.commit()
        return int(cur.lastrowid)


async def delete_annotation(ann_id: int) -> bool:
    """删除标注，返回是否存在。"""
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute("DELETE FROM annotations WHERE id = ?", (ann_id,))
        await db.commit()
        return cur.rowcount > 0


async def list_annotations_by_book(book_id: int) -> list[dict]:
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT a.id, a.book_id, a.book_para_id, "
            "  a.offset_start, a.offset_end, a.color, a.note_text, a.created_at, "
            "  bp.text AS para_text, bc.seq AS chapter_seq "
            "FROM annotations a "
            "LEFT JOIN book_paras bp ON bp.id = a.book_para_id "
            "LEFT JOIN book_chapters bc ON bc.id = bp.chapter_id "
            "WHERE a.book_id = ? ORDER BY a.created_at DESC, a.id DESC",
            (book_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def list_all_annotations() -> list[dict]:
    """全部标注（含书标题、封面），供 /annotations 页按书聚合。"""
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT a.id, a.book_id, a.book_para_id, "
            "  a.offset_start, a.offset_end, a.color, a.note_text, a.created_at, "
            "  bp.text AS para_text, bc.seq AS chapter_seq, "
            "  b.title AS book_title, b.cover_url "
            "FROM annotations a "
            "LEFT JOIN book_paras bp ON bp.id = a.book_para_id "
            "LEFT JOIN book_chapters bc ON bc.id = bp.chapter_id "
            "LEFT JOIN books b ON b.id = a.book_id "
            "ORDER BY a.created_at DESC, a.id DESC"
        )
        return [dict(r) for r in await cur.fetchall()]


def _escape_like(q: str) -> str:
    r"""LIKE 通配符转义：\ % _ 分别转 \\% \\_，保证字面匹配。"""
    return (q.replace("\\", "\\\\")
              .replace("%", "\\%")
              .replace("_", "\\_"))


async def search_book_paras(q: str, top_k: int = 10) -> list[dict]:
    """M5 搜索占位：SQLite LIKE（关键词），M6 再上语义。"""
    q = (q or "").strip()
    if not q:
        return []
    top_k = max(1, min(top_k, 50))
    pattern = f"%{_escape_like(q)}%"
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bp.id AS para_id, bp.book_id, bp.chapter_id, "
            "  bp.seq AS para_seq, bp.text AS para_text, bp.start_ts, bp.end_ts, "
            "  bc.title AS chapter_title, bc.seq AS chapter_seq, "
            "  b.title AS book_title, b.cover_url "
            "FROM book_paras bp "
            "JOIN books b ON b.id = bp.book_id "
            "JOIN book_chapters bc ON bc.id = bp.chapter_id "
            "WHERE bp.text LIKE ? ESCAPE '\\' "
            "ORDER BY b.created_at DESC, bp.id LIMIT ?",
            (pattern, top_k),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    return rows

