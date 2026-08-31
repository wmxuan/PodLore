"""FunASR 转写封装：SenseVoiceSmall + fsmn-vad（2 模型方案，无 ct-punc）。

方案来源：M0 已验证的 backend/scripts/validate_asr_pipeline.py（RTF 0.17，
300s 音频全流水线约 53s CPU），本模块是其正式化实现：
- ① 解码：m4a 经 imageio-ffmpeg（静态二进制）转 16k 单声道 wav（已是 16k mono 则直读）
- ② 长音频按 10 分钟切片，逐片 VAD + 逐段转写，段起止时间加切片偏移拼接
- ③ 时间戳来自 fsmn-vad 段起止（模型无关），SenseVoice 本身不输出时间戳
- ④ SenseVoiceSmall int8 量化加载，use_itn=True 自带标点（勿接 ct-punc，会双重标点）
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
import numpy as np
import soundfile as sf
from loguru import logger

from app.infra.config import REPO_ROOT, apply_modelscope_cache_env

SLICE_SEC = 600  # 长音频切片：10 分钟/片
MIN_SEG_SEC = 0.1  # <0.1s 的 VAD 碎片跳过

_DEFAULT_ASR_MODEL = "iic/SenseVoiceSmall"
_DEFAULT_VAD_MODEL = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"

_RICH_TAG = re.compile(r"<\|[^|]*\|>")  # SenseVoice rich 标签，如 <|zh|><|NEUTRAL|><|Speech|>

_models: dict | None = None  # 惰性加载单例：{"vad": AutoModel, "asr": AutoModel}


def _apply_env_defaults() -> None:
    """把 .env 的 ASR 配置读入环境（不覆盖外部已设置的值）。

    解析规则：忽略注释行与行内注释（` #` 之后）、忽略空值。
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split("#", 1)[0].strip()  # 剥离行内注释
        if value:
            os.environ.setdefault(key.strip(), value)


def init_asr() -> None:
    """惰性加载 2 个模型（首次调用后缓存；幂等）。"""
    global _models
    if _models is not None:
        return
    _apply_env_defaults()
    apply_modelscope_cache_env()  # 统一模型缓存到 data/models/（须在 funasr 加载前）
    device = os.environ.get("ASR_DEVICE", "cpu")
    from funasr import AutoModel  # 延迟导入：避免拖慢应用启动

    t0 = time.time()
    vad = AutoModel(
        model=os.environ.get("ASR_VAD_MODEL", _DEFAULT_VAD_MODEL),
        device=device, disable_update=True,
    )
    asr = AutoModel(
        model=os.environ.get("ASR_MODEL", _DEFAULT_ASR_MODEL),
        device=device, disable_update=True, quantize=True,  # int8：运行时内存约 240MB
    )
    _models = {"vad": vad, "asr": asr}
    import platform
    import resource  # 记录运行时内存峰值（macOS 单位 bytes，Linux 单位 KB）

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = rss / (1024 * 1024) if platform.system() == "Darwin" else rss / 1024
    logger.info(f"ASR 模型加载完成（device={device}，耗时 {time.time() - t0:.1f}s，进程峰值内存 {peak_mb:.0f}MB，含 fp32→int8 加载瞬态）")


def is_loaded() -> bool:
    """模型是否已加载。"""
    return _models is not None


def strip_rich(text: str) -> str:
    """清洗 SenseVoice 输出中的 rich 标签。"""
    return _RICH_TAG.sub("", text).strip()


def _ensure_wav16k(audio_path: Path) -> Path:
    """返回 16k 单声道 wav。已是则原样返回；m4a/其他格式经 imageio-ffmpeg 转换（结果缓存）。"""
    try:
        info = sf.info(str(audio_path))
        if info.samplerate == 16000 and info.channels == 1:
            return audio_path
    except Exception:  # noqa: BLE001  soundfile 不识别的格式（m4a 等）→ 走 ffmpeg
        pass

    out = audio_path.with_name(f"{audio_path.stem}_16k.wav")
    if out.exists() and out.stat().st_size > 0:
        return out  # 已转换过，直接复用
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [ffmpeg, "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1",
         "-sample_fmt", "s16", str(out), "-loglevel", "error"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 解码失败（{audio_path.name}）：{result.stderr[:300]}")
    logger.info(f"m4a 解码完成：{audio_path.name} → {out.name}")
    return out


def _slice_ranges(total_sec: float, slice_sec: int = SLICE_SEC) -> list[tuple[float, float]]:
    """长音频切片范围：600s → 1 片；1500s → [0,600][600,1200][1200,1500]。"""
    if total_sec <= 0:
        return []
    ranges = []
    beg = 0.0
    while beg < total_sec:
        ranges.append((beg, min(beg + slice_sec, total_sec)))
        beg += slice_sec
    return ranges


def _transcribe_slice(chunk: np.ndarray, sr: int, offset: float) -> list[dict]:
    """单切片：VAD 分段 → 逐段 SenseVoice → 组装 [{text, start, end}]（绝对时间 = 偏移 + 段相对时间）。"""
    segments: list[dict] = []
    # VAD 走临时 wav 文件（与 M0 验证行为一致）
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        sf.write(tmp.name, chunk, sr)
        tmp.close()
        vad_res = _models["vad"].generate(input=tmp.name, batch_size_s=300)
    finally:
        os.unlink(tmp.name)

    vad_segments = vad_res[0]["value"] if vad_res and vad_res[0].get("value") else []
    for beg_ms, end_ms in vad_segments:
        asr_chunk = chunk[beg_ms * sr // 1000 : end_ms * sr // 1000]
        if asr_chunk.size < sr * MIN_SEG_SEC:
            continue
        try:
            asr_res = _models["asr"].generate(
                input=np.ascontiguousarray(asr_chunk), fs=sr, cache={},
                language="zh", use_itn=True,  # use_itn 自带标点，勿接 ct-punc
            )
        except Exception as e:  # noqa: BLE001 单段失败不中断整集（M2 错误处理约定）
            logger.warning(f"段转写失败（{offset + beg_ms / 1000:.1f}s）：{type(e).__name__}: {e}")
            continue
        text = strip_rich(asr_res[0]["text"])
        if text:
            segments.append({
                "text": text,
                "start": round(offset + beg_ms / 1000, 2),
                "end": round(offset + end_ms / 1000, 2),
            })
    return segments


def transcribe(audio_path: Path, progress_cb: Callable[[float, float], None] | None = None) -> list[dict]:
    """音频 → 带时间戳分段列表 [{text, start, end}]（时间戳秒，来自 VAD）。

    progress_cb(processed_sec, total_sec)：每片处理完回调一次，供进度展示。
    """
    init_asr()
    wav = _ensure_wav16k(Path(audio_path))
    data, sr = sf.read(str(wav), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    total_sec = len(data) / sr

    segments: list[dict] = []
    for beg, end in _slice_ranges(total_sec):
        chunk = data[int(beg * sr):int(end * sr)]
        segments.extend(_transcribe_slice(chunk, sr, offset=beg))
        if progress_cb:
            progress_cb(end, total_sec)
    return segments


def segment_to_paras(segments: list[dict], max_chars: int = 200,
                     min_chars: int = 50) -> list[dict]:
    """把 ASR 分段合并为阅读友好段落（50-200 字）。

    合并规则：按句号/问号/叹号断句，累积满 min_chars 且遇句末标点则成段；
    无句末标点累积到 max_chars 强切成段。段落 start 取首段 start，end 取末段 end。
    """
    paras: list[dict] = []
    buf: list[dict] = []
    for seg in sorted(segments, key=lambda s: s["start"]):
        buf.append(seg)
        joined = "".join(b["text"] for b in buf)
        sentence_end = joined.endswith(("。", "！", "？", "!", "?"))
        if (len(joined) >= min_chars and sentence_end) or len(joined) >= max_chars:
            paras.append({"text": joined, "start": buf[0]["start"], "end": buf[-1]["end"]})
            buf = []
    if buf:  # 尾部不足 min_chars 的残留也成段
        joined = "".join(b["text"] for b in buf)
        paras.append({"text": joined, "start": buf[0]["start"], "end": buf[-1]["end"]})
    return paras
