"""M2 测试：transcribe_service（mock 转写，验证状态流转 / 进度 / 段落落库 / 防重入）。

mock 约定：worker 通过 asr.transcribe 模块引用调用 → 统一 patch app.infra.asr.transcribe。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.infra import asr as asr_mod
from app.infra import db
from app.services import transcribe_service as svc
from tests.conftest import run

EID = "a" * 24


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PODLORE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("AUDIO_DIR", str(tmp_path / "audio"))
    run(db.init_db())
    yield


@pytest.fixture()
def episode_row(tmp_db, tmp_path: Path):
    """入库一个单集，audio_path 指向一个 >1MB 的本地假文件。"""
    fake_audio = tmp_path / "ep.m4a"
    fake_audio.write_bytes(b"\x00" * 1_100_000)
    run(db.upsert_episode({
        "eid": EID, "pid": "p" * 24, "title": "测试单集", "duration": 300,
        "audio_url": "https://media.example.com/ep.m4a",
        "audio_path": str(fake_audio),
        "podcast": {"pid": "p" * 24, "title": "测试节目", "author": "作者",
                    "brief": None, "cover_url": None},
    }))
    return run(db.get_episode(EID))


FAKE_SEGMENTS = [
    {"text": "第一句话，内容完整。", "start": 0.0, "end": 10.0},
    {"text": "第二句话，内容完整。", "start": 10.0, "end": 20.0},
    {"text": "第三句话，内容完整。", "start": 20.0, "end": 30.0},
]


class TestWorkerSuccess:
    def test_status_flow_and_paras(self, episode_row, monkeypatch):
        monkeypatch.setattr(asr_mod, "transcribe", lambda p, progress_cb=None: FAKE_SEGMENTS)

        svc.start_transcribe(EID).result(timeout=30)

        row = run(db.get_episode(EID))
        assert row["transcript_status"] == "done"
        assert row["transcript_progress"] == 1.0

        paras = run(db.get_transcript_paras(row["id"]))
        assert len(paras) == 1  # 3 段各 10 字 → 30 字 < 50 → 合 1 段
        assert paras[0]["seq"] == 1
        assert paras[0]["start_ts"] == 0.0
        assert paras[0]["end_ts"] == 30.0

    def test_re_transcribe_replaces_paras(self, episode_row, monkeypatch):
        monkeypatch.setattr(asr_mod, "transcribe", lambda p, progress_cb=None: FAKE_SEGMENTS)
        svc.start_transcribe(EID).result(timeout=30)

        # 二次转写：换一批分段 → 旧段落被整体替换（幂等）
        monkeypatch.setattr(
            asr_mod, "transcribe",
            lambda p, progress_cb=None: [
                {"text": f"这是重转写后的第{i}句话。" * 2, "start": i * 5.0, "end": i * 5.0 + 4.0}
                for i in range(3)
            ],
        )
        svc.start_transcribe(EID).result(timeout=30)

        row = run(db.get_episode(EID))
        paras = run(db.get_transcript_paras(row["id"]))
        assert len(paras) == 1
        assert paras[0]["text"].startswith("这是重转写后的第0句话。")


class TestWorkerFailure:
    def test_transcribe_error_sets_failed(self, episode_row, monkeypatch):
        def boom(path, progress_cb=None):
            raise RuntimeError("模型炸了")

        monkeypatch.setattr(asr_mod, "transcribe", boom)
        svc.start_transcribe(EID).result(timeout=30)
        assert run(db.get_episode(EID))["transcript_status"] == "failed"


class TestWorkerGuard:
    def test_skip_when_processing(self, episode_row):
        run(db.update_transcript_status(EID, "processing"))
        svc._worker(EID)  # 直接调 worker：应防重入 early return
        row = run(db.get_episode(EID))
        assert row["transcript_status"] == "processing"  # 未被改成 failed/done

    def test_unknown_eid_noop(self, tmp_db):
        svc._worker("f" * 24)  # 无该行，静默跳过不抛错


class TestWorkerDownload:
    def test_download_when_audio_missing(self, episode_row, tmp_path, monkeypatch):
        # 本地文件改为无效 → 触发下载分支
        missing = tmp_path / "nope.m4a"
        run(db.update_audio_path(EID, str(missing)))

        calls = {}

        def fake_download(url, dest, progress_cb=None):
            calls["url"] = url
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"\x00" * 1_100_000)
            return Path(dest)

        monkeypatch.setattr(svc.downloader, "download_audio", fake_download)
        monkeypatch.setattr(asr_mod, "transcribe", lambda p, progress_cb=None: FAKE_SEGMENTS)

        svc.start_transcribe(EID).result(timeout=30)
        assert calls["url"] == "https://media.example.com/ep.m4a"
        row = run(db.get_episode(EID))
        assert row["transcript_status"] == "done"
        assert row["audio_path"].endswith(f"{EID}.m4a")  # 下载路径已回写


class TestGetTranscript:
    def test_done_returns_paras(self, episode_row, monkeypatch):
        monkeypatch.setattr(asr_mod, "transcribe", lambda p, progress_cb=None: FAKE_SEGMENTS)
        svc.start_transcribe(EID).result(timeout=30)

        result = run(svc.get_transcript(EID))
        assert result["status"] == "done"
        assert result["progress"] == 1.0
        assert len(result["paras"]) == 1
        assert result["title"] == "测试单集"

    def test_pending_returns_empty_paras(self, episode_row):
        result = run(svc.get_transcript(EID))
        assert result["status"] == "pending"
        assert result["paras"] == []

    def test_unknown_eid_returns_none(self, tmp_db):
        assert run(svc.get_transcript("f" * 24)) is None
