"""小宇宙数据抓取：URL 解析 / 页面抓取 / __NEXT_DATA__ 提取 / 元数据归一化。

数据源事实（M0 预研实测，样本见 backend/tests/fixtures/episode_page.html）：
- 单集页 URL：https://www.xiaoyuzhoufm.com/episode/{eid}，eid 为 24 位 hex
- 页面含 <script id="__NEXT_DATA__">，元数据在 props.pageProps.episode
- 音频直链：episode.enclosure.url（m4a），media.source.url 为同值兜底
- 封面：podcast.image.picUrl（注意：指令文档写的 podcast.cover 实际不存在），兜底 og:image
- 无需登录，GET + 浏览器 UA 即可
"""

from __future__ import annotations

import json
import re

import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15.0
SITE_BASE = "https://www.xiaoyuzhoufm.com"

_EPISODE_ID_RE = re.compile(r"/episode/([0-9a-fA-F]{24})")
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
_OG_IMAGE_RE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"')
_SERIES_RE = re.compile(r"^EP\s*\d+\s*(.+?)｜")  # 基础版：EP{n} {系列}｜{副标题}


def parse_episode_url(url: str) -> str:
    """从各种形态的小宇宙链接（含 query 参数、hash、富文本粘贴）提取单集 eid。"""
    m = _EPISODE_ID_RE.search(url)
    if not m:
        raise ValueError(f"无法从小宇宙链接解析 eid：{url}")
    return m.group(1).lower()


def fetch_episode_page(eid: str) -> str:
    """GET 单集页面 HTML（浏览器 UA，15s 超时）。"""
    url = f"{SITE_BASE}/episode/{eid}"
    resp = httpx.get(
        url,
        headers={"User-Agent": BROWSER_UA},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text


def extract_next_data(html: str) -> dict:
    """从页面 HTML 提取 __NEXT_DATA__ JSON 并解析为 dict。"""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("页面中未找到 __NEXT_DATA__（页面结构可能已变化）")
    return json.loads(m.group(1))


def extract_series_name(title: str) -> str | None:
    """基础版系列名提取：`EP{n} {系列}｜{副标题}` → 系列（第二期 M9 完善）。"""
    m = _SERIES_RE.match(title.strip())
    return m.group(1).strip() if m else None


def _cover_url(episode: dict, html: str | None) -> str | None:
    """封面提取：优先 podcast.image.picUrl，兜底页面 og:image meta。"""
    image = (episode.get("podcast") or {}).get("image")
    if isinstance(image, dict) and image.get("picUrl"):
        return image["picUrl"]
    if isinstance(image, str) and image:
        return image
    if html:
        m = _OG_IMAGE_RE.search(html)
        if m:
            return m.group(1)
    return None


def extract_episode_meta(next_data: dict, html: str | None = None) -> dict:
    """从 __NEXT_DATA__ 提取单集元数据，字段归一化为 snake_case。

    返回字段：eid / pid / title / description / duration / pub_date /
    audio_url / audio_size / shownotes_html / play_count / clap_count /
    favorite_count / comment_count / series_name / cover_url / podcast(子 dict)。
    """
    try:
        episode = next_data["props"]["pageProps"]["episode"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"__NEXT_DATA__ 结构不符合预期（缺少 episode 节点）：{e}") from e

    podcast = episode.get("podcast") or {}
    enclosure = episode.get("enclosure") or {}
    media = episode.get("media") or {}
    title = episode.get("title") or ""
    cover = _cover_url(episode, html)

    return {
        "eid": episode.get("eid"),
        "pid": episode.get("pid") or podcast.get("pid"),
        "title": title,
        "description": episode.get("description"),
        "duration": episode.get("duration"),
        "pub_date": episode.get("pubDate"),
        "audio_url": enclosure.get("url") or (media.get("source") or {}).get("url"),
        "audio_size": media.get("size"),
        "shownotes_html": episode.get("shownotes"),
        "play_count": episode.get("playCount"),
        "clap_count": episode.get("clapCount"),
        "favorite_count": episode.get("favoriteCount"),
        "comment_count": episode.get("commentCount"),
        "series_name": extract_series_name(title),
        "cover_url": cover,
        "podcast": {  # 节目级信息，upsert_episode 时顺带入库 podcasts 表
            "pid": podcast.get("pid"),
            "title": podcast.get("title"),
            "author": podcast.get("author"),
            "brief": podcast.get("brief") or podcast.get("description"),
            "cover_url": cover,
        },
    }


def fetch_episode(url_or_eid: str) -> dict:
    """一步到位：小宇宙链接（或裸 eid）→ 抓取页面 → 解析 → 归一化元数据。"""
    text = url_or_eid.strip()
    eid = parse_episode_url(text) if "/" in text else text.lower()
    html = fetch_episode_page(eid)
    return extract_episode_meta(extract_next_data(html), html=html)
