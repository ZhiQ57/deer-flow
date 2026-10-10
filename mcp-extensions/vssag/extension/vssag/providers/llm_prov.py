from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ..errors import ConfigurationError, ProviderError


class LLMProvider(Protocol):
    """VSSAG 文本生成服务的最小异步协议。"""

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        ...


class OpenAILLMProvider:
    """通过 OpenAI SDK 调用 OpenAI 兼容 Chat Completions 接口。"""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gpt-4o-mini",
        base_url: str | None = None,
        *,
        extra_body: Mapping[str, Any] | None = None,
        client: Any | None = None,
    ):
        """初始化 OpenAI 兼容聊天客户端。

        Args:
            api_key: 供应商 API 密钥。
            model_name: 供应商侧模型名称或部署 ID。
            base_url: OpenAI 兼容 API 根地址。
            extra_body: 供应商扩展请求参数。
            client: 可选的 OpenAI 异步客户端，用于依赖注入和测试。

        Raises:
            ConfigurationError: API 密钥或模型名无效。
        """

        if not api_key and client is None:
            raise ConfigurationError("LLM provider requires an API key")
        if not model_name.strip():
            raise ConfigurationError("LLM provider requires a model name")
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url or None
        self.extra_body = dict(extra_body or {})
        self._client = client

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        """发送一次非流式文本生成请求。

        Args:
            prompt: 用户提示词。
            **kwargs: 兼容 Chat Completions 的请求参数。

        Returns:
            模型返回的文本内容。

        Raises:
            ProviderError: 远端请求失败或响应缺少文本内容。
        """

        if not prompt.strip():
            raise ValueError("LLM prompt must not be empty")
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise ConfigurationError(
                    "LLM provider requires the vssag providers extra"
                ) from exc

            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        request = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.get("temperature", 0.0),
        }
        extra_body = dict(self.extra_body)
        call_extra_body = kwargs.get("extra_body")
        if call_extra_body is not None:
            if not isinstance(call_extra_body, Mapping):
                raise TypeError("extra_body must be a mapping")
            extra_body.update(call_extra_body)
        if extra_body:
            request["extra_body"] = extra_body
        for name in (
            "max_tokens",
            "response_format",
            "top_p",
            "stop",
            "seed",
        ):
            if kwargs.get(name) is not None:
                request[name] = kwargs[name]
        try:
            response = await self._client.chat.completions.create(**request)
        except Exception as exc:
            raise ProviderError(
                "LLM request failed: "
                f"model={self.model_name!r}, base_url={self.base_url or 'default'}"
            ) from exc
        choices = list(getattr(response, "choices", ()) or ())
        if not choices:
            raise ProviderError(
                f"LLM response contained no choices: model={self.model_name!r}"
            )
        content = getattr(getattr(choices[0], "message", None), "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            return "".join(text_parts)
        raise ProviderError(
            f"LLM response contained no text content: model={self.model_name!r}"
        )
