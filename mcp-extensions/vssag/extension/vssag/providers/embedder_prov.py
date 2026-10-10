"""Embedding provider protocols and OpenAI-compatible adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from ..errors import ConfigurationError, ProviderError


class Embedder(Protocol):
    """VSSAG 向量模型的最小异步协议。"""

    dimension: int
    model_name: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class OpenAIEmbedder:
    """通过 OpenAI SDK 调用 OpenAI 兼容 Embedding 接口。"""

    def __init__(
        self,
        api_key: str,
        model_name: str = "text-embedding-3-small",
        dimension: int = 1536,
        base_url: str | None = None,
        *,
        batch_size: int = 10,
        send_dimensions: bool = True,
        client: Any | None = None,
    ):
        """初始化 OpenAI 兼容 Embedding 客户端。

        Args:
            api_key: 供应商 API 密钥。
            model_name: 供应商侧 Embedding 模型名称或部署 ID。
            dimension: 期望的向量维度。
            base_url: OpenAI 兼容 API 根地址。
            batch_size: 单次请求的最大文本数量。
            send_dimensions: 是否向服务发送 dimensions 参数。
            client: 可选的 OpenAI 异步客户端，用于依赖注入和测试。

        Raises:
            ConfigurationError: API 密钥、模型名或批大小无效。
        """

        if not api_key and client is None:
            raise ConfigurationError("Embedding provider requires an API key")
        if not model_name.strip():
            raise ConfigurationError("Embedding provider requires a model name")
        if batch_size < 1:
            raise ConfigurationError("Embedding batch size must be positive")
        self.api_key = api_key
        self.model_name = model_name
        self.dimension = int(dimension)
        self.base_url = base_url or None
        self.batch_size = batch_size
        self.send_dimensions = send_dimensions
        self._client = client

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量生成向量并保持输入顺序。

        Args:
            texts: 待向量化的文本序列。

        Returns:
            与输入顺序一致的浮点向量列表。

        Raises:
            ProviderError: 远端请求失败或响应数量与输入不一致。
        """

        values = list(texts)
        if not values:
            return []
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise ConfigurationError(
                    "Embedding provider requires the vssag providers extra"
                ) from exc

            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        results: list[list[float]] = []
        for start in range(0, len(values), self.batch_size):
            batch = values[start : start + self.batch_size]
            request: dict[str, Any] = {
                "model": self.model_name,
                "input": batch,
            }
            if self.send_dimensions and self.dimension:
                request["dimensions"] = self.dimension
            try:
                response = await self._client.embeddings.create(**request)
            except Exception as exc:
                raise ProviderError(
                    "Embedding request failed: "
                    f"model={self.model_name!r}, base_url={self.base_url or 'default'}"
                ) from exc

            data = list(getattr(response, "data", ()) or ())
            if len(data) != len(batch):
                raise ProviderError(
                    "Embedding response count does not match request: "
                    f"expected={len(batch)}, actual={len(data)}, "
                    f"model={self.model_name!r}"
                )
            if all(getattr(item, "index", None) is not None for item in data):
                data.sort(key=lambda item: int(item.index))
            results.extend(list(item.embedding) for item in data)
        return results
