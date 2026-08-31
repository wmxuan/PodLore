"""音频流式下载：断点续传（HTTP Range）+ 大小校验 + 进度回调。

续传策略：dest 已存在 → 携带 Range: bytes={size}- 请求；
- 服务器返回 206 → 追加写入（续传成功）
- 服务器返回 200（不支持 Range）→ 重头写入
- 返回 416（本地已完整）→ 直接视为完成
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx

from app.infra.fetch_xyz import BROWSER_UA

CHUNK_SIZE = 64 * 1024
CONNECT_TIMEOUT = 15.0  # 连接超时 15s（读超时 60s，大文件流式）


def validate_audio(path: Path, min_bytes: int = 1_000_000) -> bool:
    """音频文件有效性：存在且大小 >= 1MB。"""
    return path.exists() and path.stat().st_size >= min_bytes


def download_audio(url: str, dest: Path,
                   progress_cb: Callable[[int, int], None] | None = None) -> Path:
    """流式下载音频到 dest（m4a 直链），支持断点续传。返回 dest。

    progress_cb(received_bytes, total_bytes)：total 未知时为 -1。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    resume_from = dest.stat().st_size if dest.exists() else 0
    headers = {"User-Agent": BROWSER_UA}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"

    received = resume_from
    total = -1
    with httpx.stream(
        "GET", url, headers=headers, follow_redirects=True,
        timeout=httpx.Timeout(CONNECT_TIMEOUT, read=60.0),
    ) as resp:
        if resp.status_code == 416:
            return dest  # 本地文件已完整
        if resume_from and resp.status_code == 200:
            received, resume_from = 0, 0  # 服务器不支持 Range：重头
        resp.raise_for_status()

        content_range = resp.headers.get("content-range", "")
        content_length = resp.headers.get("content-length")
        if "/" in content_range:
            total = int(content_range.rsplit("/", 1)[-1])
        elif content_length:
            total = received + int(content_length)

        with open(dest, "ab" if resume_from else "wb") as f:
            for chunk in resp.iter_bytes(CHUNK_SIZE):
                f.write(chunk)
                received += len(chunk)
                if progress_cb:
                    progress_cb(received, total)
    return dest
