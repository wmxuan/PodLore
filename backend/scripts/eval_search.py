"""M6 搜索评测脚本：跑 eval/dataset/search_queries.jsonl → Recall@K & MRR & 失败 case。

两种模式（二选一，CLI --mode 指定）：
  direct   直接 import app.infra.* 调用（不启动 HTTP，适合 CI / 本地）
  http     通过 FastAPI TestClient 或 base_url 调 /api/search（默认 direct）

指标：
  Recall@K : query 粒度。对每条 query，expected_book_ids 集合 S，top-K 返回中出现的 book_id 为 T。
              Recall@K = |S ∩ T| / |S|。
              然后整体 Recall@K（**报告口径**）：Recall@K 等于 1.0 的 query 数 / 总 query 数。
              （验收线要求 "Recall@10 ≥ 0.7" —— 即 30+ 条 query 里 70% 以上要把 expected_book_ids 全召回）
  MRR      : 对每条 query，取 expected_book_ids 中最早出现的 rank r，贡献 1/r。
  Hit@K(book) : 更宽松，top-K 中只要包含任意 expected 即算 1。

输出：
  文本报告 + 可选 JSON 报告文件。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EVAL_DATASET = REPO_ROOT / "eval" / "dataset" / "search_queries.jsonl"


# ---------- Search runner ----------

@dataclass
class SearchHit:
    rank: int
    book_id: int | None
    para_id: int | None
    score: float
    engine_hit: str | None
    book_title: str | None
    para_text: str | None


@dataclass
class SearchResult:
    q: str
    engine: str
    embedding_ready: bool
    embedding_error: str | None
    hits: list[SearchHit] = field(default_factory=list)


SearchFn = Callable[[str, int], SearchResult]


def _load_queries(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    out = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "query" not in obj or "expected_book_ids" not in obj:
                raise ValueError(f"L{i}: 缺少 query/expected_book_ids")
            out.append(obj)
    return out


# ---------- Metrics ----------

@dataclass
class PerQueryResult:
    query: dict
    result: SearchResult
    S: set[int]
    T: set[int]
    recall_set_cover: float      # |S∩T| / |S|
    first_hit_rank: int | None   # 第一条命中任意 expected 的 rank（1-based；None 表示全漏）
    passed_full_cover: bool      # recall_set_cover == 1.0


def _eval_one(query: dict, result: SearchResult, top_k: int) -> PerQueryResult:
    S = set(int(x) for x in query.get("expected_book_ids") or [])
    hits_in_k = result.hits[:top_k]
    T = set(h.book_id for h in hits_in_k if h.book_id is not None)
    if not S:
        cover = 1.0
    else:
        cover = len(S & T) / len(S)
    first_rank: int | None = None
    for h in hits_in_k:
        if h.book_id in S:
            first_rank = h.rank
            break
    return PerQueryResult(
        query=query, result=result, S=S, T=T,
        recall_set_cover=round(cover, 4),
        first_hit_rank=first_rank,
        passed_full_cover=abs(cover - 1.0) < 1e-9,
    )


@dataclass
class Summary:
    total: int
    top_k: int
    recall_full_cover_rate: float        # Recall@K（验收口径：query recall_set_cover=1 的比例）
    hit_any_rate: float                  # Hit@K（有任意 expected 出现的 query 比例）
    mrr: float
    avg_set_cover: float                 # 平均 |S∩T|/|S|
    passed_queries: int
    failed_cases: list[PerQueryResult]


def _summarize(results: list[PerQueryResult], top_k: int) -> Summary:
    total = len(results)
    if total == 0:
        return Summary(0, top_k, 0.0, 0.0, 0.0, 0.0, 0, [])
    full = sum(1 for r in results if r.passed_full_cover)
    hit_any = sum(1 for r in results if r.first_hit_rank is not None)
    mrr = 0.0
    for r in results:
        if r.first_hit_rank:
            mrr += 1.0 / r.first_hit_rank
    mrr /= total
    avg_cover = sum(r.recall_set_cover for r in results) / total
    failed = [r for r in results if not r.passed_full_cover]
    failed.sort(key=lambda r: (r.recall_set_cover, -(r.first_hit_rank or 10**9)))
    return Summary(
        total=total, top_k=top_k,
        recall_full_cover_rate=round(full / total, 4),
        hit_any_rate=round(hit_any / total, 4),
        mrr=round(mrr, 4),
        avg_set_cover=round(avg_cover, 4),
        passed_queries=full,
        failed_cases=failed,
    )


# ---------- Runner: direct (async, no HTTP) ----------

async def _direct_search(q: str, top_k: int, engine: str) -> SearchResult:
    """直接调用 search_api.search。先尝试触发 embedding 初始化 + vector build。"""
    from app.api.search_api import search, admin_search_rebuild
    # 评测前先 admin rebuild（保证 FTS + 向量都加载最新；embedding 失败不抛错）
    try:
        await admin_search_rebuild()
    except Exception:
        pass
    r = await search(q=q, top_k=top_k, engine=engine, include_context=False)
    hits: list[SearchHit] = []
    for i, row in enumerate(r.get("results") or [], 1):
        hits.append(SearchHit(
            rank=i,
            book_id=row.get("book_id"),
            para_id=row.get("para_id"),
            score=float(row.get("score") or 0.0),
            engine_hit=row.get("engine_hit"),
            book_title=row.get("book_title"),
            para_text=row.get("para_text"),
        ))
    return SearchResult(
        q=q, engine=r.get("engine", "?"),
        embedding_ready=bool(r.get("embedding_ready")),
        embedding_error=r.get("embedding_error"),
        hits=hits,
    )


def _make_direct_runner(engine: str) -> SearchFn:
    def _run(q: str, top_k: int) -> SearchResult:
        return asyncio.run(_direct_search(q, top_k, engine))
    return _run


# ---------- Runner: HTTP ----------

def _make_http_runner(base_url: str, engine: str) -> SearchFn:
    try:
        import httpx  # 非强依赖，未装时报错
    except Exception as e:
        raise RuntimeError(f"--mode http 需要 httpx：`{sys.executable} -m pip install httpx`") from e

    def _run(q: str, top_k: int) -> SearchResult:
        with httpx.Client(timeout=120) as c:
            resp = c.get(
                base_url.rstrip("/") + "/api/search",
                params={"q": q, "top_k": top_k, "engine": engine, "include_context": 0},
            )
            resp.raise_for_status()
            r = resp.json()
        hits: list[SearchHit] = []
        for i, row in enumerate(r.get("results") or [], 1):
            hits.append(SearchHit(
                rank=i, book_id=row.get("book_id"), para_id=row.get("para_id"),
                score=float(row.get("score") or 0.0), engine_hit=row.get("engine_hit"),
                book_title=row.get("book_title"), para_text=row.get("para_text"),
            ))
        return SearchResult(q=q, engine=r.get("engine", "?"),
                            embedding_ready=bool(r.get("embedding_ready")),
                            embedding_error=r.get("embedding_error"), hits=hits)
    return _run


# ---------- Report ----------

def _format_report(queries: list[dict], per: list[PerQueryResult],
                   summary: Summary, top_k: int, engine: str) -> str:
    lines: list[str] = []
    L = lines.append
    L(f"==== PodLore M6 Search Evaluation Report ====")
    L(f"dataset      : {EVAL_DATASET}  ({len(queries)} queries)")
    L(f"top_k        : {top_k}")
    L(f"engine       : {engine}")
    # embedding 状态（从任一非空 result 拿）
    sample = next((x.result for x in per if x.result is not None), None)
    if sample:
        L(f"embedding    : ready={sample.embedding_ready} error={sample.embedding_error}")
        L(f"search_engine: {sample.engine}")
    L("")
    L("-- 指标（验收用） --")
    L(f"Recall@{top_k}（验收口径：full-set-cover query 数 / 总数）= {summary.recall_full_cover_rate}  ({summary.passed_queries}/{summary.total})")
    L(f"Hit@{top_k}（任意 expected 出现比例）                 = {summary.hit_any_rate}")
    L(f"平均 expected_book_ids 覆盖率                          = {summary.avg_set_cover}")
    L(f"MRR                                                    = {summary.mrr}")
    L(f"验收线 Recall@{top_k} >= 0.7                          = {'PASS ✅' if summary.recall_full_cover_rate >= 0.7 else 'FAIL ❌（见失败 case 分析）'}")
    L("")
    if not summary.failed_cases:
        L("-- 失败 case: 无 --")
    else:
        L(f"-- 失败 case ({len(summary.failed_cases)} 条，按严重度升序) --")
        for r in summary.failed_cases:
            q = r.query
            cover = r.recall_set_cover
            fr = r.first_hit_rank
            top_hit = r.result.hits[:3]
            L("-" * 68)
            L(f"Q: {q['query']}  [type={q.get('type','?')}]  expected_book_ids={q['expected_book_ids']}")
            L(f"   recall_set_cover={cover}  first_hit_rank={fr}")
            if r.S and r.S - r.T:
                L(f"   缺失 book_id: {sorted(r.S - r.T)}")
            if top_hit:
                L(f"   top-3 实际命中：")
                for h in top_hit:
                    snip = (h.para_text or "")[:40].replace("\n", " ")
                    L(f"     rank={h.rank} book={h.book_id} score={h.score:.3f} engine={h.engine_hit} | {snip}")
            else:
                L("   top-3: 无结果")
    L("")
    return "\n".join(lines)


# ---------- CLI ----------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(EVAL_DATASET))
    ap.add_argument("--top-k", type=int, default=10, help="Recall@K 口径（验收用 10）")
    ap.add_argument("--engine", default="hybrid", choices=["hybrid", "vector", "fts", "like"])
    ap.add_argument("--mode", default="direct", choices=["direct", "http"])
    ap.add_argument("--base-url", default="http://127.0.0.1:8101", help="mode=http 时后端地址")
    ap.add_argument("--report-json", default="", help="可选：输出 JSON 报告到文件")
    args = ap.parse_args()

    queries = _load_queries(Path(args.dataset))
    if args.mode == "direct":
        runner = _make_direct_runner(args.engine)
    else:
        runner = _make_http_runner(args.base_url, args.engine)

    per: list[PerQueryResult] = []
    print(f"[eval] {len(queries)} queries, engine={args.engine}, top_k={args.top_k}")
    for i, q in enumerate(queries, 1):
        try:
            result = runner(q["query"], args.top_k)
        except Exception as e:
            result = SearchResult(q=q["query"], engine=f"error:{type(e).__name__}",
                                  embedding_ready=False, embedding_error=str(e))
        per.append(_eval_one(q, result, args.top_k))
        passed = per[-1].passed_full_cover
        if i % 5 == 0 or not passed:
            print(f"  [{i:02d}/{len(queries)}] cover={per[-1].recall_set_cover} first={per[-1].first_hit_rank} pass={'OK' if passed else 'MISS'} q={q['query'][:28]}")

    summary = _summarize(per, args.top_k)
    report_txt = _format_report(queries, per, summary, args.top_k, args.engine)
    print(report_txt)

    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as f:
            json.dump({
                "dataset": str(args.dataset),
                "top_k": args.top_k,
                "engine_requested": args.engine,
                "engine_actual": getattr(per[0].result, "engine", None) if per else None,
                "summary": {
                    "total": summary.total,
                    "Recall@K": summary.recall_full_cover_rate,
                    "Hit@K": summary.hit_any_rate,
                    "avg_set_cover": summary.avg_set_cover,
                    "MRR": summary.mrr,
                    "passed_queries": summary.passed_queries,
                    "threshold_pass": summary.recall_full_cover_rate >= 0.7,
                },
                "cases": [
                    {
                        "query": r.query,
                        "recall_set_cover": r.recall_set_cover,
                        "first_hit_rank": r.first_hit_rank,
                        "passed": r.passed_full_cover,
                        "top_results": [
                            {"rank": h.rank, "book_id": h.book_id, "score": h.score,
                             "engine_hit": h.engine_hit, "book_title": h.book_title,
                             "para_text": h.para_text,
                             } for h in r.result.hits[:5]
                        ],
                    } for r in per
                ],
            }, f, ensure_ascii=False, indent=2)

    return 0 if summary.recall_full_cover_rate >= 0.7 else 1


if __name__ == "__main__":
    raise SystemExit(main())
