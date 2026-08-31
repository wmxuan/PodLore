"""成书服务：把转写稿（应用 edits）冻结成 books/book_chapters/book_paras 独立快照。

约束（不可违背）：
- 三表与 transcript_paras 完全分离；建书后不随后续转写变化。
- 同集重复建书 → 新 book_id（version+1），**不覆盖旧书**。
- edits 数组：[{para_seq, action:'keep'|'replace'|'delete', new_text?}]
"""

from __future__ import annotations

from typing import Any

from app.infra import db

_VALID_ACTIONS = {"keep", "replace", "delete"}


class BookValidationError(ValueError):
    """edits 输入校验异常（para_seq 不存在 / action 不合法）。"""


def _apply_edits(paras: list[dict], edits: list[dict] | None) -> list[dict]:
    """应用 edits，返回保留的段落（文本已替换）。

    - 未在 edits 中出现的段落：默认 keep（用户没改就保留）
    - para_seq 不存在：抛 BookValidationError（避免用户以为已删除但没删）
    - delete：从结果中移除；replace：替换 text；keep：原样
    - 结果仍按原顺序（不重排）
    """
    edits = edits or []
    para_index = {p["seq"]: p for p in paras}

    action_by_seq: dict[int, dict] = {}
    for e in edits:
        try:
            seq = int(e["para_seq"])
        except (TypeError, ValueError, KeyError):
            raise BookValidationError(f"edits 项缺少合法 para_seq：{e}")
        if seq not in para_index:
            raise BookValidationError(f"para_seq={seq} 在转写稿中不存在")
        act = str(e.get("action", "keep"))
        if act not in _VALID_ACTIONS:
            raise BookValidationError(f"action 必须是 keep/replace/delete：{e}")
        if act == "replace" and ("new_text" not in e or not isinstance(e["new_text"], str)):
            raise BookValidationError(f"replace 必须带 new_text：{e}")
        # 同一 seq 出现多次，后者覆盖前者
        action_by_seq[seq] = {"action": act, "new_text": e.get("new_text")} if act == "replace" \
            else {"action": act}

    kept: list[dict] = []
    for p in paras:
        op = action_by_seq.get(p["seq"])
        if op is None:
            kept.append(dict(p))
            continue
        if op["action"] == "delete":
            continue
        if op["action"] == "replace":
            kept.append({**p, "text": op["new_text"],
                         "start_ts": p["start_ts"], "end_ts": p["end_ts"]})
        else:
            kept.append(dict(p))
    return kept


def _assign_chapters(kept_paras: list[dict], outline: list[dict]) -> tuple[list[dict], list[int]]:
    """按 episode_outline.start_ts/end_ts 区间分配章节；无 outline 兜底单章「全文」。

    返回 (chapters, chapter_idxs)：chapters=[{title, start_ts, end_ts}], idxs=kept_paras 对应下标。
    """
    if not outline:
        chapters = [{"title": "全文",
                     "start_ts": kept_paras[0]["start_ts"] if kept_paras else 0,
                     "end_ts": kept_paras[-1]["end_ts"] if kept_paras else 0}]
        return chapters, [0] * len(kept_paras)

    chapters = [{"title": o["title"], "start_ts": float(o["start_ts"]),
                 "end_ts": float(o["end_ts"])} for o in outline]
    # 若段落 start_ts 落在某章 [start, end) → 归属该章；否则选最近的上一章或下一章（单调不后退）
    idxs: list[int] = []
    for p in kept_paras:
        found = 0
        for i, ch in enumerate(chapters):
            if ch["start_ts"] <= p["start_ts"] < ch["end_ts"]:
                found = i
                break
        else:
            # 兜底：取 start_ts 最接近的章节
            best_d, best_i = 1e18, 0
            for i, ch in enumerate(chapters):
                d = abs((ch["start_ts"] + ch["end_ts"]) / 2 - p["start_ts"])
                if d < best_d:
                    best_d, best_i = d, i
            found = best_i
        idxs.append(found)
    # 去重空章：如果某章没有任何段落 → 标题保留但段落为 0 也没问题
    return chapters, idxs


# ---------------- 公开接口 ----------------

async def create_book(eid: str, edits: list[dict] | None = None) -> dict[str, Any]:
    """创建冻结快照。返回 {id, version, chapter_count, para_count, title, cover_url}。"""
    ep = await db.get_episode(eid)
    if ep is None:
        raise BookValidationError(f"单集不存在：{eid}")
    paras = await db.get_transcript_paras(ep["id"])
    if not paras:
        raise BookValidationError(f"该单集尚无转写稿，无法成书：{eid}")

    kept = _apply_edits(paras, edits)
    if not kept:
        raise BookValidationError("应用 edits 后无任何段落，无法成书")

    outline = await db.get_outline(ep["id"])
    chapters, idxs = _assign_chapters(kept, outline)

    # 实际有内容的章节（保留原顺序）
    used_chapters: list[dict] = []
    chapter_map: dict[int, int] = {}
    for i, ch in enumerate(chapters):
        if i in idxs:
            chapter_map[i] = len(used_chapters)
            used_chapters.append(ch)
    if not used_chapters:
        used_chapters = [{"title": "全文",
                          "start_ts": kept[0]["start_ts"], "end_ts": kept[-1]["end_ts"]}]
        idxs = [0] * len(kept)
        chapter_map = {0: 0}

    mapped_idxs = [chapter_map[i] for i in idxs]

    book_id = await db.insert_book(
        ep["id"], ep["title"], ep["cover_url"],
        len(used_chapters), len(kept),
    )
    chapter_ids = await db.insert_book_chapters(book_id, used_chapters)
    await db.insert_book_paras(book_id, chapter_ids, [
        {
            "chapter_idx": mapped_idxs[i],
            "seq": i + 1,
            "text": kept[i]["text"],
            "start_ts": float(kept[i]["start_ts"]),
            "end_ts": float(kept[i]["end_ts"]),
        }
        for i in range(len(kept))
    ])

    head = await db.get_book_header(book_id)
    assert head is not None
    return head


async def get_book(book_id: int) -> dict[str, Any] | None:
    """返回书全文：header + chapters（附带段落）。"""
    header = await db.get_book_header(book_id)
    if header is None:
        return None
    chapters = await db.get_book_chapters(book_id)
    paras = await db.get_book_paras(book_id)
    ch_by_id = {c["id"]: c for c in chapters}
    out_chapters: dict[int, dict] = {}
    for c in chapters:
        out_chapters[c["id"]] = {
            "id": c["id"], "seq": c["seq"], "title": c["title"], "paras": [],
        }
    for p in paras:
        c = out_chapters[p["chapter_id"]]
        c["paras"].append({
            "id": p["id"], "seq": p["seq"], "text": p["text"],
            "start_ts": p["start_ts"], "end_ts": p["end_ts"],
        })
    header["chapters"] = [out_chapters[c["id"]] for c in chapters]
    return header


async def list_books() -> list[dict]:
    """书架卡片列表。"""
    return await db.list_books()
