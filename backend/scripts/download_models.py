"""下载 FunASR 中文转写模型（paraformer-zh）。

供 install.sh 调用；modelscope 下载自带进度条。
模型缓存重定向到工作区 data/models/（沙箱/主目录权限友好，且 data/ 已 gitignore）。
"""

import os
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


def main() -> None:
    print(f"模型缓存目录：{MODELS_DIR}")
    print("开始下载/加载 FunASR paraformer-zh 模型（首次约 1GB，请耐心等待）...")
    # M2 才做惰性加载封装，此处仅做模型下载与可用性验证
    AutoModel(
        model="paraformer-zh",
        device="cpu",
        disable_update=True,
        cache_dir=str(MODELS_DIR / "funasr"),
    )
    print("✅ FunASR paraformer-zh 模型下载并加载成功")


if __name__ == "__main__":
    main()
