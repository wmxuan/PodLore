"""pytest 共享 fixture：真实页面解析出的元数据、本地 Range HTTP 服务。"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# 测试库统一走临时路径，避免污染 data/podlore.db
os.environ.setdefault("PODLORE_DB", "data/test_podlore.db")

# 让 backend/scripts/* 可作为命名空间包导入（如 eval_search 测试）：
# pytest 默认只把 conftest 所在目录（backend/tests）加入 sys.path，repo root 不在，
# 导致 `from backend.scripts import eval_search` 报 ModuleNotFoundError。这里补上。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def run(coro):
    """在同步测试中执行协程（不引入 pytest-asyncio 依赖）。"""
    return asyncio.run(coro)


@pytest.fixture(scope="session")
def fixture_html() -> str:
    return (FIXTURES_DIR / "episode_page.html").read_text(encoding="utf-8")


class _RangeHandler(BaseHTTPRequestHandler):
    """支持 Range 请求的静态文件服务（用于断点续传测试）。"""

    data: bytes = b""
    range_hits: list[str | None] = []

    def do_GET(self):  # noqa: N802
        rng = self.headers.get("Range")
        _RangeHandler.range_hits.append(rng)
        data = _RangeHandler.data
        if rng and rng.startswith("bytes="):
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            body = data[start:]
        else:
            self.send_response(200)
            body = data
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture()
def range_server():
    """本地 HTTP 服务，返回 (base_url, payload, range_hits 引用)。"""
    payload = random.randbytes(1_200_000)  # 1.2MB，满足 validate_audio 阈值
    _RangeHandler.data = payload
    _RangeHandler.range_hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/audio.m4a", payload, _RangeHandler.range_hits
    finally:
        server.shutdown()
