"""M1 测试：音频下载（本地 Range 服务：全量 / 断点续传 / 已完整 / 无效 URL）。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.infra.downloader import download_audio, validate_audio


class TestDownloadAudio:
    def test_full_download(self, range_server, tmp_path):
        url, payload, _ = range_server
        dest = tmp_path / "ep.m4a"
        events: list[tuple[int, int]] = []
        out = download_audio(url, dest, progress_cb=lambda r, t: events.append((r, t)))
        assert out == dest
        assert dest.read_bytes() == payload
        assert validate_audio(dest)
        assert events and events[-1] == (len(payload), len(payload))  # 进度回调最终对齐

    def test_resume_from_partial(self, range_server, tmp_path):
        url, payload, hits = range_server
        dest = tmp_path / "ep.m4a"
        # 模拟上次中断：本地已有前 60%
        cut = int(len(payload) * 0.6)
        dest.write_bytes(payload[:cut])

        download_audio(url, dest)

        assert dest.read_bytes() == payload  # 内容完整无缝拼接
        assert any(h and h.startswith(f"bytes={cut}") for h in hits)  # 发起了 Range 续传

    def test_already_complete_416(self, range_server, tmp_path):
        url, payload, hits = range_server
        dest = tmp_path / "ep.m4a"
        dest.write_bytes(payload)  # 本地已完整 → Range 超出 → 416 → 幂等返回
        out = download_audio(url, dest)
        assert out == dest
        assert dest.read_bytes() == payload
        assert hits[-1] == f"bytes={len(payload)}-"

    def test_server_without_range_support(self, range_server, tmp_path, monkeypatch):
        url, payload, _ = range_server
        dest = tmp_path / "ep.m4a"
        dest.write_bytes("残留旧数据".encode("utf-8"))  # 有本地残留
        # 模拟服务器不支持 Range：剥离 Range 头，让服务端返回 200
        import app.infra.downloader as dl

        real_stream = httpx.stream

        def strip_range(method, url, headers=None, **kw):
            headers = dict(headers or {})
            headers.pop("Range", None)
            return real_stream(method, url, headers=headers, **kw)

        monkeypatch.setattr(dl.httpx, "stream", strip_range)
        download_audio(url, dest)
        assert dest.read_bytes() == payload  # 重头下载覆盖残留

    def test_invalid_url_raises(self, tmp_path):
        with pytest.raises(httpx.HTTPError):
            download_audio("http://127.0.0.1:1/audio.m4a", tmp_path / "x.m4a")


class TestValidateAudio:
    def test_small_file_invalid(self, tmp_path):
        p = tmp_path / "small.m4a"
        p.write_bytes(b"x" * 100)
        assert not validate_audio(p)

    def test_missing_file_invalid(self, tmp_path):
        assert not validate_audio(tmp_path / "nope.m4a")
