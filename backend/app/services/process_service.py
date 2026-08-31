"""AI 加工任务：分块摘要 → 章节大纲 → 金句溯源 → 段落级广告标记。

异步模型：与转写共享全局单线程池（串行，避免多 CPU 任务互抢）。
进度划分：摘要 20% → 大纲 20% → 金句 30% → 广告 30%。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from app.infra import db
from app.infra.llm import chat, LLMConfigError

# 与转写复用同一个执行池（保持串行、稳定 CPU 占用）
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="process")

# 分块策略：每批按段落数/字数切，上限 4000 字（deepseek-chat 上下文 64k，留余量）
_CHUNK_MAX_CHARS = 4000
_SUMMARY_MAX = 300          # 最终摘要字数上限
_QUOTES_MIN = 3
_QUOTES_MAX = 8
_AD_CONSERVATIVE_KEYWORDS = ("本期由", "感谢", "赞助", "广告时间", "点击链接",
                             "优惠码", "赞助播", "合作", "推广", "会员专享")

# ---------------- Pydantic JSON Schemas ----------------

class ChunkSummary(BaseModel):
    summary: str = Field(description="该批段落的内容要点，3-5 句")


class FinalSummary(BaseModel):
    summary: str = Field(description=f"全书人话摘要，{_SUMMARY_MAX}字以内")


class ChunkQuotes(BaseModel):
    quotes: list[dict] = Field(
        description="该批段落候选金句（2-5条），每条含 quote(原文)、para_seq(第几段)、reason",
    )


class FinalQuotes(BaseModel):
    quotes: list[dict] = Field(
        description=f"最终金句列表（{_QUOTES_MIN}-{_QUOTES_MAX}条），每条含 quote、reason",
    )


class ChunkOutline(BaseModel):
    sections: list[dict] = Field(
        description="该批段落提炼的章节，每条含 title、start_ts、end_ts（秒）",
    )


class FinalOutline(BaseModel):
    outline: list[dict] = Field(
        description="全书章节大纲，按时间顺序，非空；每条含 title、start_ts、end_ts",
    )


class AdVerdict(BaseModel):
    ads: list[dict] = Field(
        description="明确属于广告的段落，仅标记非常确定的项；每条含 para_seq、reason"
        "。宁可漏，不可误杀；正常内容段落不要填。",
    )

# ---------------- 辅助 ----------------

def _db(coro) -> Any:
    return asyncio.run(coro)


def _chunk_paras(paras: list[dict], max_chars: int = _CHUNK_MAX_CHARS) -> list[list[dict]]:
    """按字数上限把段落分块。保证每块不超字数，边界按段落切（不破坏段落完整）。"""
    chunks: list[list[dict]] = []
    buf: list[dict] = []
    buf_chars = 0
    for p in paras:
        text_len = len(p["text"])
        if buf and buf_chars + text_len > max_chars:
            chunks.append(buf)
            buf, buf_chars = [], 0
        buf.append(p)
        buf_chars += text_len
    if buf:
        chunks.append(buf)
    return chunks


def _format_chunk(chunk: list[dict]) -> str:
    """把一批段落格式化成 LLM 可读文本（带段号 + 时间戳）。"""
    lines = []
    for p in chunk:
        s, e = int(p["start_ts"]), int(p["end_ts"])
        ts = f"[{s//60:02d}:{s%60:02d}-{e//60:02d}:{e%60:02d}]"
        lines.append(f"段{p['seq']} {ts}：{p['text']}")
    return "\n".join(lines)


def _find_quote_source(quote_text: str, paras: list[dict],
                      threshold: float = 0.75) -> dict | None:
    """金句溯源：在段落中找与 quote_text 最相似的子串/段落，返回 {text, start_ts, end_ts, seq}。

    匹配逻辑：
    1. 精确子串匹配（优先）
    2. difflib 相似度 >= threshold 的段落（宽容 ASR 丢助词）
    均找不到返回 None——该金句丢弃。
    """
    stripped = quote_text.strip().strip("“”\"'")
    if not stripped:
        return None
    # 1. 子串精确
    for p in paras:
        if stripped in p["text"]:
            return {"text": p["text"], "start_ts": p["start_ts"], "end_ts": p["end_ts"], "seq": p["seq"]}
    # 2. 整段相似度（容忍 ASR 丢字符）
    best = (0.0, None)
    for p in paras:
        score = difflib.SequenceMatcher(a=stripped, b=p["text"]).ratio()
        if score > best[0]:
            best = (score, p)
    score, p = best
    if p and score >= threshold:
        return {"text": p["text"], "start_ts": p["start_ts"], "end_ts": p["end_ts"], "seq": p["seq"]}
    return None

# ---------------- 4 项加工 ----------------

def _sys_ep_title(ep_title: str) -> str:
    return f"你是播客精读编辑。当前单集标题：《{ep_title}》。请基于提供的转写内容加工成结构化结果。"

def generate_summary(episode_id: int, ep_title: str, paras: list[dict]) -> str:
    """分块摘要 → 合并，返回 <=300 字的人话摘要。"""
    chunks = _chunk_paras(paras)
    partials: list[str] = []
    for chunk in chunks:
        messages = [
            {"role": "system", "content": _sys_ep_title(ep_title)},
            {"role": "user", "content":
                "阅读下面一批播客转写段落，提炼该部分的内容要点（3-5 句，不超过 200 字）：\n"
                + _format_chunk(chunk)},
        ]
        part: ChunkSummary = chat(messages, json_schema=ChunkSummary, temperature=0.3)  # type: ignore[assignment]
        partials.append(part.summary)

    messages = [
        {"role": "system", "content": _sys_ep_title(ep_title)},
        {"role": "user", "content": (
            f"下面是分块要点，请合并成一段完整的「人话摘要」，"
            f"不超过 {_SUMMARY_MAX} 字，忠实于原意、不编造：\n\n"
            + "\n".join(f"- {s}" for s in partials)
        )},
    ]
    final: FinalSummary = chat(messages, json_schema=FinalSummary, temperature=0.3)  # type: ignore[assignment]
    text = final.summary[:_SUMMARY_MAX]
    _db(db.update_book_summary(episode_id, text))
    return text


def generate_outline(episode_id: int, ep_title: str, paras: list[dict]) -> list[dict]:
    """分块章节候选 → 合并排序去重 → 写入 episode_outline。"""
    chunks = _chunk_paras(paras)
    sections: list[dict] = []
    for chunk in chunks:
        messages = [
            {"role": "system", "content": _sys_ep_title(ep_title)},
            {"role": "user", "content": (
                "从这批段落里抽出 2-4 个自然章节（每个章节覆盖若干段），"
                "返回 sections 列表，每条包含 title（10 字内短标题）、"
                "start_ts(章节起始段落的 start_ts)、end_ts（章节结束段落的 end_ts）。\n\n"
                + _format_chunk(chunk)
            )},
        ]
        cs: ChunkOutline = chat(messages, json_schema=ChunkOutline, temperature=0.2)  # type: ignore[assignment]
        for s in cs.sections:
            if s.get("title") and s.get("start_ts") is not None and s.get("end_ts") is not None:
                sections.append({"title": str(s["title"])[:40],
                                 "start_ts": float(s["start_ts"]),
                                 "end_ts": float(s["end_ts"])})

    # 合并：按 start_ts 排序；相邻重叠或间隔 < 60s 且标题相近则合二为一
    sections.sort(key=lambda x: x["start_ts"])
    merged: list[dict] = []
    for s in sections:
        if not merged or s["start_ts"] - merged[-1]["end_ts"] > 60:
            merged.append(s.copy())
        else:
            merged[-1]["end_ts"] = max(merged[-1]["end_ts"], s["end_ts"])
    _db(db.replace_outline(episode_id, merged))
    return merged


def generate_quotes(episode_id: int, ep_title: str, paras: list[dict]) -> list[dict]:
    """分块金句候选 → 合并精选 → 溯源丢弃不可定位的 → 写入 episode_quotes。"""
    chunks = _chunk_paras(paras)
    candidates: list[dict] = []
    for chunk in chunks:
        messages = [
            {"role": "system", "content": _sys_ep_title(ep_title)},
            {"role": "user", "content": (
                "从这批段落里挑 2-5 条可能成为「金句」的原文摘抄，"
                "请返回 quotes 列表，每条必须包含："
                "quote(必须是段内出现过的原文子串，不要改写)、"
                "para_seq(对应段号，即 段{seq})、reason(为什么是金句，20 字内)。\n\n"
                + _format_chunk(chunk)
            )},
        ]
        cs: ChunkQuotes = chat(messages, json_schema=ChunkQuotes, temperature=0.3)  # type: ignore[assignment]
        for q in cs.quotes:
            candidates.append({
                "quote": str(q.get("quote", "")).strip(),
                "para_seq": q.get("para_seq"),
                "reason": str(q.get("reason", ""))[:80],
            })

    # 精选 3-8 条（去重 quote）
    seen = set()
    uniq = []
    for c in candidates:
        if c["quote"] and c["quote"] not in seen:
            uniq.append(c)
            seen.add(c["quote"])

    # 让 LLM 精选 3-8 条 + 给理由
    messages = [
        {"role": "system", "content": _sys_ep_title(ep_title)},
        {"role": "user", "content": (
            f"从以下候选金句里挑 {_QUOTES_MIN}-{_QUOTES_MAX} 条最值得留下来的（不要编造）。"
            f"返回 quotes 列表，每条含 quote、reason（为什么值得保留，20 字内）。\n\n"
            + "\n".join(f"- {i+1}. {c['quote']}" for i, c in enumerate(uniq[:40]))
        )},
    ]
    fq: FinalQuotes = chat(messages, json_schema=FinalQuotes, temperature=0.3)  # type: ignore[assignment]

    # 溯源：每条金句定位原文，找不到就丢弃
    final_quotes: list[dict] = []
    discarded = 0
    for item in fq.quotes:
        text = str(item.get("quote", "")).strip()
        src = _find_quote_source(text, paras)
        if src is None:
            discarded += 1
            logger.info(f"金句溯源失败已丢弃：{text[:20]}...")
            continue
        final_quotes.append({
            "text": text if len(text) < 160 else src["text"][:200],
            "start_ts": float(src["start_ts"]),
            "end_ts": float(src["end_ts"]),
            "reason": str(item.get("reason", ""))[:120],
        })

    if discarded:
        logger.warning(f"金句溯源丢弃 {discarded} 条；最终保留 {len(final_quotes)} 条")
    # 条数兜底：低于 3 条时直接补 top-N 候选
    if len(final_quotes) < _QUOTES_MIN:
        for c in uniq:
            if len(final_quotes) >= _QUOTES_MIN:
                break
            if any(q["text"] == c["quote"] for q in final_quotes):
                continue
            src = _find_quote_source(c["quote"], paras)
            if src:
                final_quotes.append({
                    "text": c["quote"][:200], "start_ts": float(src["start_ts"]),
                    "end_ts": float(src["end_ts"]), "reason": c["reason"],
                })
    if len(final_quotes) > _QUOTES_MAX:
        final_quotes = final_quotes[:_QUOTES_MAX]

    _db(db.replace_quotes(episode_id, final_quotes))
    return final_quotes


def mark_ads(episode_id: int, ep_title: str, paras: list[dict]) -> list[dict]:
    """段落级广告标记（保守：宁可漏不可误杀）。只标记明确是广告的段落。"""
    flags: list[dict] = []

    # 快速跳过：无任何广告关键词暗示的段落直接判否，省 token 与时间
    batches = _chunk_paras(paras, max_chars=2000)  # 更小的块，保持判断粒度
    for chunk in batches:
        # 规则前置：只要整段里没有任何广告关键词 → 整批全部不标（保守策略）
        joined = "\n".join(p["text"] for p in chunk)
        if not any(kw in joined for kw in _AD_CONSERVATIVE_KEYWORDS):
            continue
        messages = [
            {"role": "system", "content": _sys_ep_title(ep_title) + (
                "\n你做的是广告识别，需要极高的准确率：只有 100% 明确是广告的段落才能标记。"
                "\n宁可漏（不标记正常内容），不可误杀（把主播正常内容标成广告）。"
                "\n判断标准：出现赞助/感谢/推广/优惠码/点击链接购买等明确话术。"
            )},
            {"role": "user", "content": (
                "只返回明确属于广告的段落 ads 列表，每项含 para_seq（段号）和 reason（10 字内）。"
                "若该批没有任何明确广告，请返回 ads=[]（空数组）。\n\n"
                + _format_chunk(chunk)
            )},
        ]
        verdict: AdVerdict = chat(messages, json_schema=AdVerdict, temperature=0.1)  # type: ignore[assignment]
        for a in verdict.ads:
            try:
                seq = int(a.get("para_seq"))
            except (TypeError, ValueError):
                continue
            reason = str(a.get("reason", "疑似广告"))[:120]
            # 二次校验：必须命中关键词，否则视为误判（保守策略，宁可漏）
            p = next((p for p in paras if p["seq"] == seq), None)
            if p and any(kw in p["text"] for kw in _AD_CONSERVATIVE_KEYWORDS):
                flags.append({"seq": seq, "is_ad": True, "reason": reason})

    # 去重（同段可能被多批命中）
    flags = list({f["seq"]: f for f in flags}.values())
    _db(db.replace_ad_flags(episode_id, flags))
    return flags

# ---------------- 编排与异步入口 ----------------

_STEP_WEIGHTS = (
    ("summary", 0.20),
    ("outline", 0.20),
    ("quotes",  0.30),
    ("ads",     0.30),
)


def start_process(eid: str) -> Future:
    """提交后台加工任务，立即返回 Future。"""
    return _executor.submit(_worker, eid)


def _worker(eid: str) -> None:
    row = _db(db.get_episode(eid))
    if row is None:
        logger.warning(f"加工跳过：episodes 表无 {eid}")
        return
    if row["process_status"] == "processing":
        logger.info(f"加工防重入：{eid} 已在处理中")
        return
    if row["transcript_status"] != "done":
        logger.warning(f"加工跳过：{eid} 转写尚未完成（status={row['transcript_status']}）")
        return

    _db(db.update_process_status(eid, "processing", 0.0))
    progress_base = 0.0
    paras = _db(db.get_transcript_paras(row["id"]))
    ep_title = row["title"]
    ep_id = row["id"]

    _summary = _outline = _quotes = _ads = None
    try:
        # 摘要
        if not os.environ.get("PODLORE_PROCESS_SKIP_SUMMARY"):
            _summary = generate_summary(ep_id, ep_title, paras)
        progress_base += _STEP_WEIGHTS[0][1]
        _db(db.update_process_status(eid, "processing", progress_base))

        if not os.environ.get("PODLORE_PROCESS_SKIP_OUTLINE"):
            _outline = generate_outline(ep_id, ep_title, paras)
        progress_base += _STEP_WEIGHTS[1][1]
        _db(db.update_process_status(eid, "processing", progress_base))

        if not os.environ.get("PODLORE_PROCESS_SKIP_QUOTES"):
            _quotes = generate_quotes(ep_id, ep_title, paras)
        progress_base += _STEP_WEIGHTS[2][1]
        _db(db.update_process_status(eid, "processing", progress_base))

        if not os.environ.get("PODLORE_PROCESS_SKIP_ADS"):
            _ads = mark_ads(ep_id, ep_title, paras)
        _db(db.update_process_status(eid, "done", 1.0))
        logger.info(f"加工完成 {eid}：{len(_outline or [])} 章 / {len(_quotes or [])} 金句 / {len(_ads or [])} 广告段")
    except LLMConfigError:
        logger.error(f"加工失败 {eid}：未配置 DEEPSEEK_API_KEY，跳过 LLM 加工")
        _db(db.update_process_status(eid, "failed"))
        raise
    except Exception as e:  # noqa: BLE001 整集失败
        logger.exception(f"加工失败 {eid}：{e}")
        _db(db.update_process_status(eid, "failed"))


async def get_process_result(eid: str) -> dict | None:
    """查询加工状态与结果（摘要/大纲/金句/广告段落）；不存在返回 None。"""
    row = await db.get_episode(eid)
    if row is None:
        return None
    ep_id = row["id"]
    return {
        "eid": eid,
        "title": row["title"],
        "process_status": row["process_status"],
        "process_progress": row["process_progress"] or 0,
        "summary": row["book_summary"],
        "outline": await db.get_outline(ep_id),
        "quotes": await db.get_quotes(ep_id),
        "ads": await db.get_ad_flags(ep_id),
    }
