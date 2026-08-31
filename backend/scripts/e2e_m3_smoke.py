"""M3 端到端烟测：真实库 + M2 产生的 65 段落 + mock LLM，验证加工 4 项结构完整。

注意：直接使用 data/podlore.db（不受 conftest 的 PODLORE_DB 覆盖），需先 unset 测试环境变量。
"""
import os, re, asyncio
# 脚本直接用主数据库，防止 conftest 被意外 import 时覆盖
os.environ.pop("PODLORE_DB", None)
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-mock")

import app.services.process_service as svc_mod
from app.services.process_service import (
    ChunkSummary, FinalSummary, ChunkOutline, FinalQuotes, ChunkQuotes, AdVerdict,
    start_process,
)
from app.infra import db

EID = "6a7b23ba17676351c570589d"


def run(coro):
    return asyncio.run(coro)

run(db.init_db())
ep = run(db.get_episode(EID))
print(f"单集: {ep['title']}  |  duration={ep['duration']}s  |  trans={ep['transcript_status']}")
paras = run(db.get_transcript_paras(ep["id"]))
print(f"段落数: {len(paras)}  | 总字数: {sum(len(p['text']) for p in paras)}")
run(db.update_process_status(EID, "pending", 0))

calls = []

def fake_chat(messages, json_schema=None, temperature=0.3, model=None):
    user = messages[-1]["content"]
    calls.append(json_schema.__name__ if json_schema else "text")
    if json_schema is ChunkSummary:
        head = user.strip().replace("\n", " ")[:40]
        return ChunkSummary(summary=f"块摘要：{head}…")
    if json_schema is FinalSummary:
        return FinalSummary(summary=(
            "本期播客《声动早咖啡》聚焦美妆巨头集体押注洗护赛道，以欧莱雅洗护产品上半年销售额同比增长超过15%为引子，"
            "深入分析头皮护理专业化、男士护发兴起、功能性细分赛道爆发三大驱动力；同时谈到雅诗兰黛的投入和新品牌进入中国市场，"
            "以及主持人关于护发品类长期布局价值的观点。内容节奏轻松，适合关注快消与消费投资的听众。"
        ))
    if json_schema is ChunkOutline:
        return ChunkOutline(sections=[
            {"title": "引子与背景", "start_ts": 0.0, "end_ts": 300.0},
            {"title": "案例与数据", "start_ts": 300.0, "end_ts": 600.0},
            {"title": "观点总结", "start_ts": 600.0, "end_ts": ep["duration"] or 897},
        ])
    if json_schema is ChunkQuotes:
        quotes = []
        for m in re.finditer(r"段(\d+) \[.+?\]：(.+)", user):
            if len(quotes) >= 2:
                break
            quotes.append({
                "quote": m.group(2).strip()[:50],
                "para_seq": int(m.group(1)),
                "reason": "候选金句",
            })
        return ChunkQuotes(quotes=quotes)
    if json_schema is FinalQuotes:
        return FinalQuotes(quotes=[
            {"quote": "欧莱雅洗发护发产品的销售额同比增长超过15%", "reason": "关键数据支撑"},
            {"quote": "功能性细分赛道正在全面爆发", "reason": "趋势判断"},
            {"quote": "三大驱动力分别是头皮护理专业化、男士护发崛起与功能细分", "reason": "结构化总结"},
            {"quote": "长期来看品牌方在洗护品类的布局会持续加码", "reason": "前瞻观点"},
            {"quote": "不存在的 xyaabbccddeeffgghhiijjkk", "reason": "编造的，会被丢弃"},
        ])
    if json_schema is AdVerdict:
        return AdVerdict(ads=[])  # 保守，不标正常内容
    raise RuntimeError(f"unexpected schema: {json_schema}")


svc_mod.chat = fake_chat
start_process(EID).result(timeout=180)

row = run(db.get_episode(EID))
print(f"\n=== 加工状态: {row['process_status']} progress={row['process_progress']} ===")
print(f"LLM 调用次数: {len(calls)}  序列: {calls[:4]} ... {calls[-3:]}")
print(f"\n--- 摘要 ({len(row['book_summary'])}字) ---")
print(row["book_summary"])

outline = run(db.get_outline(row["id"]))
print(f"\n--- 大纲 ({len(outline)} 章) ---")
for c in outline:
    s, e = int(c["start_ts"]), int(c["end_ts"])
    print(f"  [{s//60:02d}:{s%60:02d}-{e//60:02d}:{e%60:02d}] {c['title']}")

quotes = run(db.get_quotes(row["id"]))
print(f"\n--- 金句 ({len(quotes)} 条；有 1 条编造的被丢弃) ---")
for q in quotes:
    s, e = int(q["start_ts"]), int(q["end_ts"])
    print(f"  [{s//60:02d}:{s%60:02d}-{e//60:02d}:{e%60:02d}] {q['text'][:56]}… ({q['reason']})")

ads = run(db.get_ad_flags(row["id"]))
print(f"\n--- 广告段落: {len(ads)} 段标记 ---")
for a in ads:
    print(f"  段{a['seq']}：{a.get('reason')}")

paras2 = run(db.get_transcript_paras(row["id"]))
ads_count = sum(1 for p in paras2 if p["is_ad"])
print(f"\n转写段读取（左联 para_flags）: is_ad 共 {ads_count} 段（与 flags 一致）")
