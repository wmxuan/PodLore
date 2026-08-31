"""下载并加载验证 FunASR 三个模型：SenseVoiceSmall + fsmn-vad + ct-punc。

供 install.sh 调用；modelscope 下载自带进度条。
模型缓存重定向到工作区 data/models/（沙箱/主目录权限友好，且 data/ 已 gitignore）。

注意：
- 必须用完整 model id（iic/xxx）。funasr 1.4.11 的短别名（如 "SenseVoiceSmall"）
  会解析成缺 org 前缀的 repo id，导致 modelscope 404（E3020）。
- ct-punc 别名实际指向 cn-en large 版（约 1.1GB）；中文播客场景用 zh-only 版
  （vocab272727，约 290MB）即可，节省磁盘与内存。
"""

import os
import sys
import time
from pathlib import Path

# 仓库根目录（backend/scripts/download_models.py → 上溯两级）
REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = Path(os.environ.get("PODLORE_MODELS_DIR", REPO_ROOT / "data" / "models"))

# 必须在导入 modelscope/funasr 之前设置缓存环境变量
os.environ["MODELSCOPE_CACHE"] = str(MODELS_DIR / "modelscope")
os.environ["HF_HOME"] = str(MODELS_DIR / "huggingface")
# modelscope 凭证文件默认写 ~/.modelscope/credentials，一并重定向进工作区
os.environ["MODELSCOPE_CREDENTIALS_PATH"] = str(MODELS_DIR / "modelscope" / "credentials")

from funasr import AutoModel  # noqa: E402

# 三个模型：主转写 + VAD 分段 + 标点恢复（对应 .env.example 与实施指令 M2 流水线）
# quantize=True：加载时量化为 int8，运行时内存约降至 1/4
MODELS = [
    ("iic/SenseVoiceSmall", "SenseVoiceSmall（主转写，fp32 约 940MB → int8 运行时约 240MB）", True),
    ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "fsmn-vad（VAD 分段+起止时间，约 1MB）", False),
    ("iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch", "ct-punc zh-only（标点恢复，约 290MB）", True),
]


def main() -> int:
    print(f"模型缓存目录：{MODELS_DIR}")
    failed = []
    for model_id, desc, quantize in MODELS:
        print(f"\n==== 下载/加载：{desc} ====")
        t0 = time.time()
        try:
            AutoModel(
                model=model_id,
                device="cpu",
                disable_update=True,
                disable_pbar=False,
                quantize=quantize,
                cache_dir=str(MODELS_DIR / "funasr"),
            )
            print(f"✅ {model_id} 下载并加载成功（耗时 {time.time() - t0:.1f}s）")
        except Exception as e:  # noqa: BLE001 逐模型报告，不中断后续
            print(f"❌ {model_id} 失败：{type(e).__name__}: {e}")
            failed.append(model_id)

    if failed:
        print(f"\n❌ 失败模型：{'、'.join(failed)}")
        return 1
    print("\n✅ 三个模型全部下载并加载成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
