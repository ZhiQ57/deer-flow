"""外部 LLM 服务协议。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class LLMProviderProtocolError(TypeError):
    """外部 LLM 服务未实现 SDK 所需调用协议时抛出的错误。"""


class LLMProviderError(RuntimeError):
    """调用外部 LLM 服务失败时抛出的错误。"""


@runtime_checkable
class LLMProvider(Protocol):
    """外部 LLM 服务同步调用协议，SDK 不内置具体模型或厂商适配器。"""

    def complete(self, prompt: str, **kwargs: Any) -> str:
        """调用外部 LLM 返回文本。

        Args:
            prompt: 提示词文本。
            **kwargs: 外部实现支持的调用参数。

        Returns:
            LLM 返回文本。
        """


@runtime_checkable
class AsyncLLMProvider(Protocol):
    """外部 LLM 服务异步调用协议，供异步链路按需实现。"""

    async def acomplete(self, prompt: str, **kwargs: Any) -> str:
        """异步调用外部 LLM 返回文本。

        Args:
            prompt: 提示词文本。
            **kwargs: 外部实现支持的调用参数。

        Returns:
            LLM 返回文本。
        """


def call_llm(provider: LLMProvider, prompt: str, **kwargs: Any) -> str:
    """调用同步 LLM provider 并校验返回文本。

    Args:
        provider: 外部注入的 LLM provider。
        prompt: 提示词文本。
        **kwargs: 外部实现支持的调用参数。

    Returns:
        LLM 返回文本。
    """
    if not str(prompt).strip():
        raise ValueError("llm prompt must not be empty")
    complete = _require_provider_method(provider, "complete")
    try:
        result = complete(prompt, **kwargs)
    except Exception as exc:
        raise LLMProviderError(f"llm provider complete(prompt) failed: {exc}") from exc
    return _normalize_llm_text(result)


async def async_call_llm(provider: AsyncLLMProvider, prompt: str, **kwargs: Any) -> str:
    """调用异步 LLM provider 并校验返回文本。

    Args:
        provider: 外部注入的异步 LLM provider。
        prompt: 提示词文本。
        **kwargs: 外部实现支持的调用参数。

    Returns:
        LLM 返回文本。
    """
    if not str(prompt).strip():
        raise ValueError("llm prompt must not be empty")
    acomplete = _require_provider_method(provider, "acomplete")
    try:
        result = await acomplete(prompt, **kwargs)
    except Exception as exc:
        raise LLMProviderError(f"llm provider acomplete(prompt) failed: {exc}") from exc
    return _normalize_llm_text(result)


def _require_provider_method(provider: object, method_name: str):
    """读取并校验外部 LLM 服务方法。"""
    method = getattr(provider, method_name, None)
    if not callable(method):
        raise LLMProviderProtocolError(f"llm provider must provide callable {method_name}")
    return method


def _normalize_llm_text(value: object) -> str:
    """校验 LLM 返回值并转换为文本。"""
    if not isinstance(value, str):
        raise LLMProviderProtocolError(f"llm provider must return str, got {type(value).__name__}")
    return value


__all__ = [
    "AsyncLLMProvider",
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderProtocolError",
    "async_call_llm",
    "call_llm",
]
