"""基础设施配置：路径与环境变量读取（轻量，无第三方依赖）。"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]  # backend/app/infra/config.py → 上溯三级


def data_dir() -> Path:
    """工作区数据目录（默认 data/，可被 DATA_DIR 覆盖）。"""
    return Path(os.environ.get("DATA_DIR", str(REPO_ROOT / "data")))


def audio_dir() -> Path:
    """音频存储目录（默认 data/audio/）。"""
    return Path(os.environ.get("AUDIO_DIR", str(data_dir() / "audio")))


def models_dir() -> Path:
    """模型缓存目录（默认 data/models/，gitignore 内）。"""
    return Path(os.environ.get("PODLORE_MODELS_DIR", str(data_dir() / "models")))


def apply_modelscope_cache_env() -> None:
    """把 modelscope/HF 缓存重定向进工作区（须在 import modelscope/funasr 前调用）。

    与 backend/scripts/download_models.py 的重定向保持一致，避免加载时漂移到
    ~/.cache/modelscope 产生双副本。
    """
    cache = models_dir()
    os.environ.setdefault("MODELSCOPE_CACHE", str(cache / "modelscope"))
    os.environ.setdefault("HF_HOME", str(cache / "huggingface"))
    os.environ.setdefault("MODELSCOPE_CREDENTIALS_PATH", str(cache / "modelscope" / "credentials"))
