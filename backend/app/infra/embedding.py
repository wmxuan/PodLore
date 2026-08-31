"""M6 本地中文语义向量：bge-small-zh-v1.5，惰性加载 + 失败降级。

设计要点（来自 M6 指令）：
- 模型：BAAI/bge-small-zh-v1.5（sentence-transformers 友好）
- 缓存重定向：data/models/sentence-transformers 下，和 M3 funasr 共 data/models
- **惰性加载**：首次 embed 调用时加载，避免 uvicorn 启动 + 测试启动慢
- **版本兼容风险已知**（M0 确认）：transformers≥5 需要 torch≥2.5，本地 torch 2.2.2。
  本环境若 `pip show transformers` 为 5.x，`import sentence_transformers` 会抛
  ImportError（torch 版本不匹配）。本模块捕获 import / SentenceTransformer 加载
  两类异常，把状态暴露给上层（`embedder_state()`），不崩溃；上层语义路径改走
  FTS/LIKE 兜底，同时在接口返回里标记 engine='fts_fallback'，便于用户决策。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .config import models_dir, apply_modelscope_cache_env


apply_modelscope_cache_env()
# sentence-transformers 默认下载也会落到 HF_HOME，这里独立目录方便排查
import os
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(models_dir() / "sentence-transformers"))

MODEL_NAME = "BAAI/bge-small-zh-v1.5"


@dataclass
class EmbedderState:
    ready: bool = False
    error: Optional[str] = None   # None/空表示没失败；否则为失败原因（给 API / 评测报告）
    dim: int = 512                # bge-small 默认 512；失败时仍返回默认方便上层 shape 校验


_state = EmbedderState()
_state_lock = threading.Lock()
_model = None   # SentenceTransformer instance，None 直到 init 成功


def embedder_state() -> EmbedderState:
    """上层检查 embedding 是否可用。用于：
    - API 返回 engine 选择 & warning
    - 评测脚本输出 embedding_enabled，不达标时可判定『语义路径未激活』
    """
    return EmbedderState(ready=_state.ready, error=_state.error, dim=_state.dim)


def _try_import_and_load() -> bool:
    """惰性执行实际加载；import + 构造器两层捕获。返回是否成功。"""
    global _model
    if _model is not None:
        return True
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as e:  # ImportError / pkg_resources / torch 版本 / transformers 5.x
        _state.error = (
            f"sentence_transformers import 失败: {type(e).__name__}: {e}. "
            f"建议降级 transformers<5（本环境 torch 2.2.2，transformers≥5 要求 torch≥2.5）"
        )
        return False
    try:
        cache_dir = models_dir() / "sentence-transformers"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _model = SentenceTransformer(
            MODEL_NAME,
            cache_folder=str(cache_dir),
        )
        # 小探测拿真实 dim（bge-small 512）
        probe = _model.encode(["probe"], convert_to_numpy=True, show_progress_bar=False)
        _state.dim = int(probe.shape[1])
        _state.ready = True
        _state.error = None
        return True
    except Exception as e:
        _state.error = (
            f"SentenceTransformer('{MODEL_NAME}') 加载失败: {type(e).__name__}: {e}"
        )
        return False


def init_embedder() -> EmbedderState:
    """显式触发加载（评测脚本 / rebuild_index 前先调一下）。返回当前状态。"""
    with _state_lock:
        if _state.ready:
            return _state
        _try_import_and_load()
        return _state


def embed(texts: list[str]) -> np.ndarray:
    """批量向量化；返回 shape=(len(texts), dim) 的 float32 矩阵。

    若 embedding 不可用，**抛 RuntimeError** —— 让上层（vector_store / search_api）
    统一回退到关键词路径；而不是返回 0 向量误导召回。
    """
    with _state_lock:
        if not _state.ready:
            ok = _try_import_and_load()
            if not ok:
                raise RuntimeError(
                    f"embedding 不可用, state={_state}. 先调用 init_embedder() 或检查依赖"
                )
    model = _model
    assert model is not None
    vecs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True,
                        show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)
