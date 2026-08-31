"""DeepSeek 封装：OpenAI 兼容协议 + JSON mode + Pydantic 校验 + 网络重试。

注：DeepSeek 官方支持 `response_format={type: "json_object"}`，返回 JSON 字符串；
本模块在此基础上叠加 Pydantic 校验——解析失败再重试 1 次，仍失败则抛错。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger
from openai import APIError, APITimeoutError, RateLimitError
from openai import OpenAI
from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "deepseek-chat"
NETWORK_RETRY_MAX = 2      # 网络错误重试 2 次
JSON_RETRY_MAX = 1         # JSON 解析/Pydantic 校验失败再重试 1 次
BACKOFF_BASE = 1.5         # 指数退避基数（秒）


class LLMConfigError(RuntimeError):
    """缺少 DEEPSEEK_API_KEY 等必要配置。"""


@dataclass
class _ClientHolder:
    """单例客户端（可被测试 monkey-patch）。"""
    instance: OpenAI | None = None


_holder = _ClientHolder()


def _build_client() -> OpenAI:
    """构造/返回 OpenAI 兼容客户端。通过 monkeypatch _holder.instance 注入 mock。"""
    if _holder.instance is not None:
        return _holder.instance
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise LLMConfigError("未配置 DEEPSEEK_API_KEY：无法生成摘要/金句等 AI 加工内容")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    _holder.instance = OpenAI(api_key=key, base_url=base, max_retries=0)
    return _holder.instance


def _retry_request(messages: list[dict], model: str, temperature: float,
                   json_mode: bool) -> str:
    """执行 chat completion：网络错误重试 NETWORK_RETRY_MAX 次（指数退避），返回内容字符串。"""
    client = _build_client()
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(NETWORK_RETRY_MAX + 1):
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except (APITimeoutError, RateLimitError, APIError) as e:
            last_err = e
            if attempt >= NETWORK_RETRY_MAX:
                break
            wait = BACKOFF_BASE ** (attempt + 1)
            logger.warning(f"LLM 网络错误（{type(e).__name__}），第 {attempt+1}/{NETWORK_RETRY_MAX} 次重试，等待 {wait:.1f}s：{e}")
            time.sleep(wait)
    assert last_err is not None
    raise last_err  # 抛出最终网络错误


def chat(messages: list[dict], json_schema: type[BaseModel] | None = None,
         temperature: float = 0.3, model: str | None = None) -> str | BaseModel:
    """DeepSeek 聊天调用。

    - json_schema=None：返回纯文本（str）。
    - json_schema 传 Pydantic 类：进入 JSON mode → 解析 → Pydantic 校验；
      校验失败再重试 1 次，仍失败抛 ValidationError。
    """
    if not messages:
        raise ValueError("chat() messages 不能为空")

    model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
    if json_schema is None:
        return _retry_request(messages, model, temperature, json_mode=False)

    # JSON mode + Pydantic 校验，最多两轮（失败重试 1 次）
    last_err: ValidationError | ValueError | None = None
    for attempt in range(JSON_RETRY_MAX + 1):
        raw = _retry_request(messages, model, temperature, json_mode=True)
        import json
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            last_err = ValueError(f"LLM 返回非 JSON：{raw[:120]}... ({e})")
            continue
        try:
            return json_schema(**parsed)
        except ValidationError as e:
            last_err = e
            logger.warning(f"Pydantic 校验失败（第 {attempt+1} 次尝试）：{e}")
    assert last_err is not None
    raise last_err
