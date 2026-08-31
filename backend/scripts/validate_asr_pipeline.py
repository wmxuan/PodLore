"""M0 提前验证：VAD 分段 → SenseVoice 转写 → ct-punc 标点 → 带时间戳输出。

对应实施指令 M2 流水线（提前暴露风险）：
① fsmn-vad 检测语音段（毫秒级起止时间）
② 逐段 SenseVoiceSmall 转写（输出无标点，含 rich 标签需清洗）
③ 逐段 ct-punc 标点恢复
④ 组装 {text, start, end}（秒），时间戳取自 VAD 段起止
⑤ 演示 segment_to_paras 段落化合并（50-200 字，start 取首段/end 取末段）

用法：
    python backend/scripts/validate_asr_pipeline.py <audio.wav> [--out result.json]
输入建议 16k 单声道 wav（外部用 ffmpeg 转换；m4a 需 ffmpeg 解码是 M0 已暴露的依赖）。
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = Path(os.environ.get("PODLORE_MODELS_DIR", REPO_ROOT / "data" / "models"))
os.environ.setdefault("MODELSCOPE_CACHE", str(MODELS_DIR / "modelscope"))
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "huggingface"))
os.environ.setdefault("MODELSCOPE_CREDENTIALS_PATH", str(MODELS_DIR / "modelscope" / "credentials"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from funasr import AutoModel  # noqa: E402

RICH_TAG = re.compile(r"<\|[^|]*\|>")  # SenseVoice rich 标签，如 <|zh|><|NEUTRAL|><|Speech|>

MODEL_ID = "iic/SenseVoiceSmall"
VAD_ID = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
PUNC_ID = "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"


def fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def strip_rich(text: str) -> str:
    """清洗 SenseVoice 输出中的 rich 标签。"""
    return RICH_TAG.sub("", text).strip()


def segment_to_paras(segments: list[dict], max_chars: int = 200, min_chars: int = 50) -> list[dict]:
    """把 ASR 分段合并为阅读友好段落：start 取首段，end 取末段（时间戳单调递增）。"""
    paras: list[dict] = []
    buf: list[dict] = []
    for seg in segments:
        buf.append(seg)
        joined = "".join(b["text"] for b in buf)
        ended = joined.endswith(("。", "！", "？", "！", "?", "!"))
        if (len(joined) >= min_chars and ended) or len(joined) >= max_chars:
            paras.append({"text": joined, "start": buf[0]["start"], "end": buf[-1]["end"]})
            buf = []
    if buf:
        joined = "".join(b["text"] for b in buf)
        paras.append({"text": joined, "start": buf[0]["start"], "end": buf[-1]["end"]})
    return paras


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path, help="16k 单声道 wav 路径")
    ap.add_argument("--out", type=Path, default=None, help="结果 JSON 输出路径")
    args = ap.parse_args()

    audio_path = args.audio.resolve()
    data, sr = sf.read(str(audio_path), dtype="float32")
    if sr != 16000:
        print(f"⚠️ 采样率 {sr} != 16000，VAD/ASR 结果可能失真")
    if data.ndim > 1:
        data = data.mean(axis=1)
    total_sec = len(data) / sr
    print(f"音频：{audio_path.name}，{total_sec:.1f}s @ {sr}Hz")

    # ---- 加载三个模型（int8）----
    print("\n==== 加载模型（SenseVoice/VAD/Punc，int8）====")
    t0 = time.time()
    vad = AutoModel(model=VAD_ID, device="cpu", disable_update=True)
    asr = AutoModel(model=MODEL_ID, device="cpu", disable_update=True, quantize=True)
    punc = AutoModel(model=PUNC_ID, device="cpu", disable_update=True, quantize=True)
    load_sec = time.time() - t0
    print(f"模型加载完成：{load_sec:.1f}s")

    # ---- ① VAD 分段 ----
    t0 = time.time()
    vad_res = vad.generate(input=str(audio_path), batch_size_s=300)
    vad_segments = vad_res[0]["value"]  # [[beg_ms, end_ms], ...]
    vad_sec = time.time() - t0
    print(f"\n==== ① VAD：{len(vad_segments)} 个语音段（{vad_sec:.1f}s）====")

    # ---- ② 逐段 SenseVoice 转写 + ③ 逐段 ct-punc 标点 + ④ 组装 ----
    results: list[dict] = []
    asr_sec = punc_sec = 0.0
    failed = 0
    t_all = time.time()
    print("\n==== ②③④ 逐段转写+标点 ====")
    for i, (beg_ms, end_ms) in enumerate(vad_segments):
        start_s, end_s = beg_ms / 1000.0, end_ms / 1000.0
        chunk = np.ascontiguousarray(data[beg_ms * sr // 1000 : end_ms * sr // 1000])
        if chunk.size < sr * 0.1:  # <0.1s 的碎片跳过
            continue
        try:
            t0 = time.time()
            asr_res = asr.generate(input=chunk, fs=sr, cache={}, language="zh", use_itn=True)
            asr_sec += time.time() - t0
            raw = asr_res[0]["text"]
            text = strip_rich(raw)
            if not text:
                continue

            t0 = time.time()
            punc_res = punc.generate(input=text)
            punc_sec += time.time() - t0
            punctuated = punc_res[0].get("text") or punc_res[0].get("value") or text

            results.append({"text": punctuated, "start": round(start_s, 2), "end": round(end_s, 2)})
            if i < 6 or i % 20 == 0:
                print(f"  [{fmt_ts(start_s)}-{fmt_ts(end_s)}] {punctuated}")
        except Exception as e:  # noqa: BLE001 单段失败不中断（M2 错误处理约定）
            failed += 1
            print(f"  ⚠️ 段 {i}（{fmt_ts(start_s)}）失败：{type(e).__name__}: {e}")
    total_sec_elapsed = time.time() - t_all
    print(f"\n转写完成：{len(results)} 段成功，{failed} 段失败")

    # ---- ⑤ 段落化 ----
    paras = segment_to_paras(results)
    print(f"\n==== ⑤ 段落化：{len(paras)} 个段落 ====")
    for p in paras[:8]:
        print(f"  [{fmt_ts(p['start'])}-{fmt_ts(p['end'])}] {p['text']}")
    if len(paras) > 8:
        print(f"  ...（共 {len(paras)} 段）")

    # ---- 统计 ----
    speech_sec = sum(r["end"] - r["start"] for r in results)
    rtf = total_sec_elapsed / total_sec if total_sec else 0
    print("\n==== 性能统计 ====")
    print(f"  音频总长 {total_sec:.1f}s，语音段合计 {speech_sec:.1f}s")
    print(f"  VAD {vad_sec:.1f}s ｜ ASR {asr_sec:.1f}s ｜ Punc {punc_sec:.1f}s（含逐段调用开销）")
    print(f"  全流水线 RTF（处理时长/音频时长）= {rtf:.2f}（<1 即实时性可用）")

    # ---- 校验时间戳单调 ----
    starts = [r["start"] for r in results]
    monotonic = all(a <= b for a, b in zip(starts, starts[1:]))
    print(f"  时间戳单调递增：{'✅' if monotonic else '❌'}")

    if args.out:
        out = {
            "audio": str(audio_path), "duration_sec": total_sec,
            "load_sec": round(load_sec, 1), "rtf": round(rtf, 3),
            "segments": results, "paras": paras,
        }
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已保存：{args.out}")

    return 0 if results and monotonic else 1


if __name__ == "__main__":
    sys.exit(main())
