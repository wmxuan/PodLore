"""PodLore · M8 全流程冒烟脚本（第一期收尾）。

一键跑通第一期全链路：
  抓取(可跳过/用 mock) → 转写(可跳过/用 mock) → 加工 → 成书 → 标注 → 搜索 → 首页四模块

每步输出 ✓/✗ + 耗时；任意 step FAIL 不立刻退出，继续跑后续步骤把状态都报出来
（除非该 step 是后续步骤的硬前置依赖——比如成书失败，标注就无法跑）。

用法：
  backend/.venv/bin/python backend/scripts/smoke_pipeline.py
  backend/.venv/bin/python backend/scripts/smoke_pipeline.py --use-existing-eid 6a7b23ba17676351c570589d
  backend/.venv/bin/python backend/scripts/smoke_pipeline.py --skip-fetch --skip-transcribe

环境：
  - 默认使用真实 data/podlore.db；不影响线上数据（不删除/不修改已有书）
  - 写入测试 episode 时用 eid=smoke + 时间戳 防冲突
  - 完成后可选择清理（--cleanup）只删 smoke episode + 关联书 + 标注
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 默认 mock 数据（避免依赖网络/ASR 模型加载）
MOCK_EID = "smoke_m8_" + str(int(time.time()))[-6:]
MOCK_EPISODE_META = {
    "pid": "smoke_pid_" * 0 + "p" * 24,
    "title": "【冒烟测试】护发行业洞察",
    "author": "smoke-author",
    "podcast": {"pid": "p" * 24, "title": "【冒烟】声动早咖啡", "author": "smoke-author"},
    "eid": MOCK_EID,
    "title": "【冒烟测试】护发行业洞察",
    "description": "smoke test episode",
    "duration": 60,
    "pub_date": "2024-01-01",
    "audio_url": "http://example.com/smoke.m4a",
    "audio_path": "",
    "cover_url": "http://example.com/smoke.jpg",
}
MOCK_PARAS = [
    {"seq": 1, "text": "大家好欢迎来到本期节目。", "start": 0.0, "end": 5.0},
    {"seq": 2, "text": "今天聊聊护发行业最近的变化。", "start": 5.0, "end": 14.0},
    {"seq": 3, "text": "欧莱雅和联合利华都在加码洗护赛道。", "start": 14.0, "end": 24.0},
    {"seq": 4, "text": "卡诗是高端护发产品线。", "start": 24.0, "end": 34.0},
    {"seq": 5, "text": "国货洗护也有差异化机会。", "start": 34.0, "end": 60.0},
]
MOCK_ANNOTATIONS = [
    {"color": "blue", "note_text": "欧莱雅护发战略"},
    {"color": "yellow", "note_text": None},
    {"color": "green", "note_text": "差异化护城河"},
]
MOCK_SEARCH_QUERY = "欧莱雅护发"


@dataclass
class StepResult:
    name: str
    ok: bool
    elapsed: float
    detail: str = ""
    output: dict | list | None = None


@dataclass
class SmokeReport:
    steps: list[StepResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def add(self, r: StepResult) -> None:
        self.steps.append(r)
        marker = "✓" if r.ok else "✗"
        print(f"  [{marker}] {r.name:<24} {r.elapsed:6.2f}s  {r.detail}")
        if not r.ok and r.output:
            print(f"        └─ output: {r.output}")


# ---------- helpers ----------

async def _step_init_db() -> StepResult:
    from app.infra import db
    t0 = time.time()
    await db.init_db()
    await db.rebuild_fts_index()
    return StepResult("init_db", True, time.time() - t0,
                      detail=f"db={db.db_path()}")


async def _step_fetch(use_existing_eid: str | None, skip: bool) -> StepResult:
    """抓取：M1/M2 的 episodes upsert。这里走 mock meta（不实际网络）。
    若 --use-existing-eid：复用已有 episode（不创建新 episode）。"""
    from app.infra import db
    t0 = time.time()
    if skip:
        return StepResult("fetch", True, time.time() - t0, detail="skipped by --skip-fetch")
    if use_existing_eid:
        ep = await db.get_episode(use_existing_eid)
        if not ep:
            return StepResult("fetch", False, time.time() - t0,
                              detail=f"existing eid not found: {use_existing_eid}")
        return StepResult("fetch", True, time.time() - t0,
                          detail=f"reuse eid={use_existing_eid} id={ep['id']}", output=ep)
    # mock upsert
    await db.upsert_episode(MOCK_EPISODE_META)
    ep = await db.get_episode(MOCK_EID)
    return StepResult("fetch", True, time.time() - t0,
                      detail=f"upsert mock eid={MOCK_EID} id={ep['id']}", output={"id": ep["id"], "eid": MOCK_EID})


async def _step_transcribe(use_existing_eid: str | None, skip: bool) -> StepResult:
    """转写：M2 的 transcript_paras。这里走 mock paras（不实际 ASR）。
    若 --use-existing-eid 且该 episode 已有 paras：复用，不覆盖。"""
    from app.infra import db
    t0 = time.time()
    if skip:
        return StepResult("transcribe", True, time.time() - t0, detail="skipped by --skip-transcribe")
    eid = use_existing_eid or MOCK_EID
    ep = await db.get_episode(eid)
    if not ep:
        return StepResult("transcribe", False, time.time() - t0, detail=f"episode not found: {eid}")
    # 已有 paras 且 --use-existing-eid → 跳过覆盖
    if use_existing_eid:
        existing = await db.get_transcript_paras(ep["id"])
        if existing:
            return StepResult("transcribe", True, time.time() - t0,
                              detail=f"reuse {len(existing)} existing paras (no overwrite)", output={"count": len(existing)})
    # 写 mock paras
    await db.replace_transcript_paras(ep["id"], MOCK_PARAS)
    return StepResult("transcribe", True, time.time() - t0,
                      detail=f"wrote {len(MOCK_PARAS)} mock paras", output={"count": len(MOCK_PARAS)})


async def _step_process() -> StepResult:
    """加工：M3 的 outline/quotes/ad_detect。
    smoke 模式跳过（不调用 LLM），只标记 status=pending，后续 freeze 不依赖。
    真实加工在 /api/process/{eid} 触发，需要 LLM API。"""
    t0 = time.time()
    return StepResult("process (LLM)", True, time.time() - t0,
                      detail="skipped (smoke 不调 LLM；真实加工走 POST /api/process/{eid})")


async def _step_freeze(use_existing_eid: str | None) -> StepResult:
    """成书：M4 book_service.create_book。"""
    from app.services import book_service
    t0 = time.time()
    eid = use_existing_eid or MOCK_EID
    book = await book_service.create_book(eid, edits=[])
    return StepResult("freeze 成书", True, time.time() - t0,
                      detail=f"book id={book['id']} title={book['title'][:24]}",
                      output={"book_id": book["id"], "title": book["title"], "eid": eid})


async def _step_annotate(book_id: int) -> StepResult:
    """标注：M5 创建 3 条 annotation（含 1 笔记）。
    用每本书的前 3 段做划线。"""
    from app.infra import db
    t0 = time.time()
    paras = await db.get_book_paras(book_id)
    if not paras:
        return StepResult("annotate", False, time.time() - t0, detail=f"no paras in book {book_id}")
    created = []
    for i, mock in enumerate(MOCK_ANNOTATIONS):
        p = paras[i % len(paras)]
        ann_id = await db.insert_annotation(
            book_id=book_id,
            book_para_id=p["id"],
            offset_start=0,
            offset_end=min(5, len(p["text"])),
            color=mock["color"],
            note_text=mock["note_text"],
        )
        created.append(ann_id)
    return StepResult("annotate", True, time.time() - t0,
                      detail=f"inserted {len(created)} annotations (含 {sum(1 for m in MOCK_ANNOTATIONS if m['note_text'])} 笔记)",
                      output={"ids": created})


async def _step_search_rebuild() -> StepResult:
    """M6 触发 admin rebuild：FTS + 向量索引。"""
    from app.api.search_api import admin_search_rebuild
    t0 = time.time()
    try:
        r = await admin_search_rebuild()
        return StepResult("search rebuild", True, time.time() - t0,
                          detail=f"embedding.ready={r['embedding']['ready']} fts={r['fts_available']} vec_n={r['vector_count']}",
                          output=r)
    except Exception as e:
        return StepResult("search rebuild", False, time.time() - t0,
                          detail=f"error: {e}", output={"error": str(e)})


async def _step_search(query: str) -> StepResult:
    """M6 搜索：GET /api/search hybrid。"""
    from app.api.search_api import search
    t0 = time.time()
    try:
        r = await search(q=query, top_k=10, engine="hybrid", include_context=False)
        books_hit = sorted({row.get("book_id") for row in r.get("results", []) if row.get("book_id")})
        return StepResult(f"search '{query}'", True, time.time() - t0,
                          detail=f"engine={r.get('engine')} total={r.get('total')} books={books_hit} embedding={r.get('embedding_ready')}",
                          output=r)
    except Exception as e:
        return StepResult(f"search '{query}'", False, time.time() - t0,
                          detail=f"error: {e}", output={"error": str(e)})


async def _step_home() -> StepResult:
    """M7 首页聚合。"""
    from app.api.home_api import get_home
    t0 = time.time()
    try:
        r = await get_home()
        return StepResult("home /api/home", True, time.time() - t0,
                          detail=f"wc={len(r['word_cloud'])} fp_pos={sum(1 for f in r['footprint'] if f['count']>0)}/30 books={len(r['books_recent'])} stats={r['stats']}",
                          output={"stats": r["stats"], "wc_top5": r["word_cloud"][:5],
                                  "books_recent_ids": [b["id"] for b in r["books_recent"]]})
    except Exception as e:
        return StepResult("home /api/home", False, time.time() - t0,
                          detail=f"error: {e}", output={"error": str(e)})


async def _step_cleanup(eid: str) -> StepResult:
    """可选清理：删除 smoke episode + 关联 book + 标注（不影响线上数据）。"""
    import aiosqlite
    from app.infra.db import db_path
    t0 = time.time()
    async with aiosqlite.connect(db_path()) as c:
        # 找 episode
        cur = await c.execute("SELECT id FROM episodes WHERE eid = ?", (eid,))
        row = await cur.fetchone()
        if not row:
            return StepResult("cleanup", True, time.time() - t0, detail=f"no smoke episode (eid={eid})")
        ep_id = row[0]
        # 删 books 关联（先删 annotations 引用 book_id，再删 book_paras/book_chapters，最后 books）
        await c.execute("DELETE FROM annotations WHERE book_id IN (SELECT id FROM books WHERE episode_id = ?)", (ep_id,))
        await c.execute("DELETE FROM book_paras WHERE book_id IN (SELECT id FROM books WHERE episode_id = ?)", (ep_id,))
        await c.execute("DELETE FROM book_chapters WHERE book_id IN (SELECT id FROM books WHERE episode_id = ?)", (ep_id,))
        await c.execute("DELETE FROM books WHERE episode_id = ?", (ep_id,))
        await c.execute("DELETE FROM transcript_paras WHERE episode_id = ?", (ep_id,))
        await c.execute("DELETE FROM episodes WHERE id = ?", (ep_id,))
        await c.commit()
    # 重建 FTS（删除后索引脏）
    from app.infra import db
    await db.rebuild_fts_index()
    return StepResult("cleanup", True, time.time() - t0, detail=f"deleted episode id={ep_id} eid={eid}")


# ---------- main ----------

async def run_smoke(args) -> int:
    print("=" * 64)
    print(f"PodLore · M8 Smoke Pipeline · started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  db: {os.environ.get('PODLORE_DB', 'data/podlore.db')}")
    print(f"  use_existing_eid: {args.use_existing_eid or '(none)'}")
    print(f"  skip_fetch={args.skip_fetch} skip_transcribe={args.skip_transcribe} cleanup={args.cleanup}")
    print("-" * 64)

    report = SmokeReport()
    eid = args.use_existing_eid

    # 1. init_db
    report.add(await _step_init_db())

    # 2. fetch
    fr = await _step_fetch(args.use_existing_eid, args.skip_fetch)
    report.add(fr)
    if not fr.ok:
        print("\n[smoke] fetch 失败，无法继续后续步骤。")
        return _summarize(report)
    if not eid:
        eid = MOCK_EID  # 用 mock 的 eid 后续步骤统一

    # 3. transcribe
    tr = await _step_transcribe(args.use_existing_eid, args.skip_transcribe)
    report.add(tr)
    if not tr.ok:
        print("\n[smoke] transcribe 失败，无法继续 freeze。")
        return _summarize(report)

    # 4. process (skip)
    report.add(await _step_process())

    # 5. freeze
    frz = await _step_freeze(args.use_existing_eid)
    report.add(frz)
    if not frz.ok:
        print("\n[smoke] freeze 失败，无法继续 annotate。")
        return _summarize(report)
    book_id = frz.output["book_id"]

    # 6. annotate
    report.add(await _step_annotate(book_id))

    # 7. search rebuild
    report.add(await _step_search_rebuild())

    # 8. search
    report.add(await _step_search(args.search_query or MOCK_SEARCH_QUERY))

    # 9. home
    report.add(await _step_home())

    # 10. cleanup（可选）
    if args.cleanup and not args.use_existing_eid:
        report.add(await _step_cleanup(MOCK_EID))

    return _summarize(report)


def _summarize(report: SmokeReport) -> int:
    print("-" * 64)
    total = len(report.steps)
    passed = sum(1 for s in report.steps if s.ok)
    elapsed_total = time.time() - report.started_at
    print(f"  total: {passed}/{total} passed  ·  elapsed {elapsed_total:.2f}s")
    fails = [s for s in report.steps if not s.ok]
    if fails:
        print(f"  FAILED: {[s.name for s in fails]}")
        return 1
    print("  RESULT: ✅ ALL GREEN")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-existing-eid", default=None,
                    help="复用已有 episode（不创建新 mock episode）；传入真实 eid")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="跳过 fetch（不创建新 episode；与 --use-existing-eid 配合）")
    ap.add_argument("--skip-transcribe", action="store_true",
                    help="跳过 transcribe（用已有 paras）")
    ap.add_argument("--search-query", default="",
                    help="搜索步骤用的 query（默认 '欧莱雅护发'）")
    ap.add_argument("--cleanup", action="store_true",
                    help="结束后清理 mock episode（仅当未 --use-existing-eid 时生效）")
    ap.add_argument("--use-prod-db", action="store_true",
                    help="使用真实 data/podlore.db（默认使用 /tmp/podlore_smoke.db 避免污染线上库）")
    args = ap.parse_args()

    # 默认使用临时 db 避免污染真实库；--use-existing-eid 或 --use-prod-db 时切换到真实库
    if not args.use_prod_db and not args.use_existing_eid:
        os.environ.setdefault("PODLORE_DB", "/tmp/podlore_smoke.db")
        # 临时库允许 cleanup（不污染真实数据）
        args.cleanup = True
    elif args.use_existing_eid and not args.use_prod_db:
        # 复用真实 episode 必须用真实库
        os.environ.pop("PODLORE_DB", None)
    return asyncio.run(run_smoke(args))


if __name__ == "__main__":
    raise SystemExit(main())
