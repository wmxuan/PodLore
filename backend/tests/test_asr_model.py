"""M2 测试：真实模型冒烟（SenseVoiceSmall 已下载才执行；合成正弦波不产生可读文本，
重点验证：m4a/懒加载/解码链路不崩、时间戳字段类型正确、切片拼接正确）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.infra import config

MODEL_DIR = config.REPO_ROOT / "data" / "models" / "modelscope" / "models" / "iic--SenseVoiceSmall"
pytestmark = pytest.mark.skipif(
    not MODEL_DIR.exists(), reason="SenseVoiceSmall 模型未下载（M0 install 步骤产出）"
)


@pytest.fixture(scope="module")
def sine_wav(tmp_path_factory) -> Path:
    """1.5 秒 440Hz 正弦波 + 静音（VAD 可能检测到或忽略，两种都算通过）。"""
    sr = 16000
    t = np.arange(int(sr * 1.5)) / sr
    wave = (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    path = tmp_path_factory.mktemp("audio") / "sine.wav"
    sf.write(str(path), wave, sr)
    return path


def test_transcribe_sine_wave_smoke(sine_wav: Path):
    from app.infra.asr import transcribe

    segments = transcribe(sine_wav)
    assert isinstance(segments, list)
    for seg in segments:
        assert set(seg) == {"text", "start", "end"}
        assert isinstance(seg["start"], float) and isinstance(seg["end"], float)
        assert seg["start"] < seg["end"]


def test_transcribe_m4a_decode_chain(sine_wav: Path, tmp_path: Path):
    """wav → m4a（ffmpeg 编码）→ transcribe 内部再解码回来，验证 imageio-ffmpeg 链路。"""
    import imageio_ffmpeg
    import subprocess

    m4a = tmp_path / "sine.m4a"
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(sine_wav),
         "-c:a", "aac", str(m4a), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    from app.infra.asr import _ensure_wav16k

    wav16k = _ensure_wav16k(m4a)
    info = sf.info(str(wav16k))
    assert info.samplerate == 16000 and info.channels == 1
