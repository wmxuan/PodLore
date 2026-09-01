"""M7 首页聚合：GET /api/home 返回 word_cloud / footprint / books_recent / stats 四模块。

数据来源（全部真实，禁止假数据）：
- word_cloud：jieba 分词 + 词频；从 annotations.note_text（笔记，×2 权重）+
  annotations.color 段原文（划线，×1 权重）+ episodes.book_summary（×1 权重）提取；
  过滤停用词；取 top 30
- footprint：近 30 天按天聚合；沉淀量 = 当天创建 books×3 + annotations×1 + notes×2
- books_recent：list_books() 前 5 本（时间倒序，已有接口）
- stats：books/annotations/notes/episodes 总数（真实 COUNT）
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter

from ..infra.config import data_dir
from ..infra.db import db_path, list_books, list_episodes


home_router = APIRouter(prefix="/api")


# ---------- 停用词 ----------

# 简明中文停用词表：覆盖常见虚词/助词/代词/常用动词/常见单字噪声
_STOPWORDS = set("""
的 了 在 是 我 有 和 就 不 人 都 一 一个 也 很 到 说 要 去 你 会 着 没 看 好
自己 这 那 这个 那个 他 她 它 我们 你们 他们 们 什么 怎么 为什么 哪 这样
没有 可以 因为 所以 但是 然后 还是 而 还 把 被 让 使 给 对 从 向 与 及 或
上 下 中 后 前 里 外 内 之 于 以 因 为 所 如 若 又 且 但 即 则 虽 然 虽 即使
个 次 些 多 少 大 小 长 短 高 低 重 轻 真 假 新 旧 第一 最后 现在 之前 以后
就是 还是 比如 例如 其实 比如 比如 通过 进行 已经 可能 应该 或者 还是 比如
嗯 啊 呢 吧 呀 哦 哈 嘛 啦 唉 哎 哇 嗯 哼 嘞 呗
一 二 三 四 五 六 七 八 九 十 百 千 万 亿
对 中 让 使 从 等 还 但 而 并 与 或 则 之 的 了 在 是 我 有 和 就 都 也 要
一些 这种 这样 那样 那种 这些 那些 怎么样 怎样 什么样 如何 为何 何以 何以
""".split())

# 过滤词：纯数字/纯标点/单字符 ASCII
def _is_noise(token: str) -> bool:
    if not token or len(token) < 2:
        return True
    if token in _STOPWORDS:
        return True
    # 全 ASCII 单字符
    if all(ord(c) < 0x80 for c in token) and len(token) < 3:
        return True
    # 含明显标点
    if re.search(r"[，。！？；：、,.!?;:\s（）()\[\]【】\"'`/\\]", token):
        return True
    # 全数字
    if re.match(r"^\d+$", token):
        return True
    return False


# ---------- jieba 切词（同步，慢启动一次） ----------
_jieba_ready = False
_jieba_lock = asyncio.Lock()


async def _ensure_jieba():
    """jieba 第一次 import 较慢（载词典 ~50ms），放 executor 不阻塞 ASGI loop。"""
    global _jieba_ready
    if _jieba_ready:
        return
    async with _jieba_lock:
        if _jieba_ready:
            return
        loop = asyncio.get_event_loop()
        def _init():
            import jieba  # noqa
            jieba.setLogLevel(60)  # 屏蔽初始化日志
            # 预热：切一次空，触发词典加载
            list(jieba.cut("预热"))
            return jieba
        await loop.run_in_executor(None, _init)
        _jieba_ready = True


async def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    await _ensure_jieba()
    loop = asyncio.get_event_loop()
    def _cut(t: str):
        import jieba
        # cut_for_search 召回更全，适合"找主题词"场景
        return [tok for tok in jieba.cut_for_search(t) if tok.strip()]
    return await loop.run_in_executor(None, _cut, text)


# ---------- 词云：从标注 + 笔记 + 摘要 提取 ----------

async def _build_word_cloud(annotations: list[dict], summaries: list[tuple[int, str]]) -> list[dict]:
    """annotations: list_all_annotations() 返回（含 note_text + para_text）。
    summaries: [(book_id, book_summary_text), ...] 从 episodes.book_summary。
    权重：note_text（笔记）×2、para_text（划线段）×1、summary ×1。
    """
    counter: Counter[str] = Counter()
    for a in annotations:
        # 笔记 ×2 权重
        if a.get("note_text"):
            toks = await _tokenize(a["note_text"])
            for t in toks:
                if _is_noise(t):
                    continue
                counter[t] += 2
        # 划线段原文 ×1
        if a.get("para_text"):
            toks = await _tokenize(a["para_text"])
            for t in toks:
                if _is_noise(t):
                    continue
                counter[t] += 1
    for _, s in summaries:
        if s:
            toks = await _tokenize(s)
            for t in toks:
                if _is_noise(t):
                    continue
                counter[t] += 1
    if not counter:
        return []
    top = counter.most_common(30)
    max_w = top[0][1] if top else 1
    # 归一化 weight 到 [0.4, 1.0] 方便前端字号映射
    return [{"word": w, "weight": round(0.4 + 0.6 * (c / max_w), 3)}
            for w, c in top]


# ---------- 足迹：按天聚合 ----------

def _date_str(ts: str | None) -> str | None:
    """把 created_at（SQLite datetime 'YYYY-MM-DD HH:MM:SS'）归一到日期 'YYYY-MM-DD'。"""
    if not ts:
        return None
    try:
        return ts[:10]
    except Exception:
        return None


async def _build_footprint(books: list[dict], annotations: list[dict]) -> list[dict]:
    """近 30 天按天聚合：沉淀量 = books×3 + annotations×1 + notes×2。
    notes 数 = annotations 中 note_text 非空条数。
    """
    today = datetime.now(timezone.utc).astimezone().date()
    start = today - timedelta(days=29)
    days: dict[str, int] = {}
    for i in range(30):
        d = (start + timedelta(days=i)).isoformat()
        days[d] = 0
    for b in books:
        d = _date_str(b.get("created_at"))
        if d in days:
            days[d] += 3
    for a in annotations:
        d = _date_str(a.get("created_at"))
        if d not in days:
            continue
        days[d] += 1
        if a.get("note_text"):
            days[d] += 2  # 笔记额外 ×2（标注本身 ×1 已计）
    return [{"date": d, "count": c} for d, c in sorted(days.items())]


# ---------- books_recent ----------

async def _books_recent(books: list[dict], limit: int = 5) -> list[dict]:
    out = []
    for b in books[:limit]:
        out.append({
            "id": b.get("id"), "title": b.get("title"),
            "cover_url": b.get("cover_url"),
            "chapters": b.get("chapter_count") or 0,
            "paras": b.get("para_count") or 0,
            "created_at": b.get("created_at"),
        })
    return out


# ---------- stats ----------

async def _stats(books: list[dict], annotations: list[dict]) -> dict:
    notes = sum(1 for a in annotations if a.get("note_text"))
    # episodes 总数：调 list_episodes 数量（已在调用方拿）
    return {
        "books": len(books),
        "annotations": len(annotations),
        "notes": notes,
        # episodes 由调用方填（避免这里再调一次）
        "episodes": 0,
    }


# ---------- 主端点 ----------

@home_router.get("/home", tags=["home"])
async def get_home():
    """聚合首页四模块数据。"""
    # 并行拉数据
    books_task = asyncio.create_task(list_books())
    anns_task = asyncio.create_task(_list_all_annotations_for_home())
    eps_task = asyncio.create_task(list_episodes())
    summaries_task = asyncio.create_task(_list_book_summaries())
    books = await books_task
    annotations = await anns_task
    episodes = await eps_task
    summaries = await summaries_task

    word_cloud, footprint, books_recent = await asyncio.gather(
        _build_word_cloud(annotations, summaries),
        _build_footprint(books, annotations),
        _books_recent(books),
    )
    stats = await _stats(books, annotations)
    stats["episodes"] = len(episodes)
    return {
        "word_cloud": word_cloud,
        "footprint": footprint,
        "books_recent": books_recent,
        "stats": stats,
    }


async def _list_all_annotations_for_home() -> list[dict]:
    """复用 db.list_all_annotations（含 note_text + para_text + created_at）。"""
    from ..infra.db import list_all_annotations
    return await list_all_annotations()


async def _list_book_summaries() -> list[tuple[int, str]]:
    """从 episodes.book_summary 取所有非空摘要（关联 book_id 通过 books.episode_id）。
    返回 [(book_id, summary_text), ...] —— 一个 episode 可能对应多本书（不同版本），
    每本都按其 episode 的 summary 计一次（保证书维度词云覆盖）。
    """
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT b.id AS book_id, e.book_summary AS summary "
            "FROM books b LEFT JOIN episodes e ON e.id = b.episode_id "
            "WHERE e.book_summary IS NOT NULL AND e.book_summary != ''"
        )
        rows = await cur.fetchall()
        return [(r["book_id"], r["summary"] or "") for r in rows]
