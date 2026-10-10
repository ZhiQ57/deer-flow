from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from ..errors import ConfigurationError, ProviderError


class Reranker(Protocol):
    """VSSAG 重排序服务的最小异步协议。"""

    async def rerank(self, query: str, texts: Sequence[str], top_k: int) -> list[tuple[int, float]]:
        ...


class DashScopeReranker:
    """封装阿里云百炼原生 Text Rerank 接口。"""

    default_endpoint = (
        "https://dashscope.aliyuncs.com/"
        "api/v1/services/rerank/text-rerank/text-rerank"
    )

    def __init__(
        self,
        api_key: str,
        model_name: str,
        endpoint: str | None = None,
        *,
        http_client: Any | None = None,
    ):
        """初始化百炼重排序适配器。

        Args:
            api_key: 百炼 API 密钥。
            model_name: 百炼重排序模型名称。
            endpoint: 百炼原生 Rerank endpoint。
            http_client: 可选的异步 HTTP 客户端，用于依赖注入和测试。

        Raises:
            ConfigurationError: API 密钥或模型名无效。
        """

        if not api_key:
            raise ConfigurationError("DashScope reranker requires an API key")
        if not model_name.strip():
            raise ConfigurationError("DashScope reranker requires a model name")
        self.api_key = api_key
        self.model_name = model_name
        self.endpoint = endpoint or self.default_endpoint
        self._http_client = http_client

    async def rerank(self, query: str, texts: Sequence[str], top_k: int) -> list[tuple[int, float]]:
        """调用百炼接口并标准化排序结果。

        Args:
            query: 用户查询。
            texts: 候选文本。
            top_k: 最大返回数量。

        Returns:
            按供应商排序后的 ``(原始索引, 相关性分数)`` 列表。

        Raises:
            ProviderError: 请求失败或响应格式不符合百炼协议。
        """

        values = list(texts)
        if not values:
            return []
        top_n = min(max(1, int(top_k)), len(values))
        payload = {
            "model": self.model_name,
            "input": {"query": query, "documents": values},
            "parameters": {
                "return_documents": False,
                "top_n": top_n,
            },
        }
        return await self._request(
            payload,
            lambda body: (
                body.get("output", {}).get("results")
                if isinstance(body.get("output"), dict)
                else body.get("results")
            ),
            top_n,
            len(values),
        )

    async def _request(
        self,
        payload,
        rows_reader,
        top_n: int,
        text_count: int,
    ) -> list[tuple[int, float]]:
        try:
            import httpx
        except ImportError as exc:
            raise ConfigurationError(
                "Rerank provider requires the vssag providers extra"
            ) from exc

        client = self._http_client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(
                self.endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            body = response.json()
            rows = rows_reader(body)
            if not isinstance(rows, list):
                raise ProviderError(
                    f"DashScope rerank response has no results: model={self.model_name!r}"
                )
            ranking = []
            for row in rows[:top_n]:
                if not isinstance(row, dict):
                    raise ProviderError(
                        "DashScope rerank result item must be an object"
                    )
                index = int(row["index"])
                score = float(row["relevance_score"])
                if index < 0 or index >= text_count:
                    raise ProviderError(
                        "DashScope rerank returned an invalid document index"
                    )
                ranking.append((index, score))
            return ranking
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "DashScope rerank request failed: "
                f"model={self.model_name!r}, endpoint={self.endpoint!r}"
            ) from exc
        finally:
            if self._http_client is None:
                await client.aclose()


class VolcengineReranker:
    """封装火山方舟 Rerank 接口。"""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        endpoint: str,
        *,
        http_client: Any | None = None,
    ):
        """初始化火山方舟重排序适配器。

        Args:
            api_key: 火山方舟 API 密钥。
            model_name: 火山方舟重排序模型或接入点 ID。
            endpoint: 火山方舟 Rerank endpoint。
            http_client: 可选的异步 HTTP 客户端，用于依赖注入和测试。

        Raises:
            ConfigurationError: API 密钥、模型名或 endpoint 无效。
        """

        if not api_key:
            raise ConfigurationError("Volcengine reranker requires an API key")
        if not model_name.strip():
            raise ConfigurationError("Volcengine reranker requires a model name")
        if not endpoint.strip():
            raise ConfigurationError("Volcengine reranker requires an endpoint")
        self.api_key = api_key
        self.model_name = model_name
        self.endpoint = endpoint
        self._http_client = http_client

    async def rerank(self, query: str, texts: Sequence[str], top_k: int) -> list[tuple[int, float]]:
        """调用火山方舟接口并标准化排序结果。

        Args:
            query: 用户查询。
            texts: 候选文本。
            top_k: 最大返回数量。

        Returns:
            按供应商排序后的 ``(原始索引, 相关性分数)`` 列表。

        Raises:
            ProviderError: 请求失败或响应格式不符合火山协议。
        """

        values = list(texts)
        if not values:
            return []
        top_n = min(max(1, int(top_k)), len(values))
        payload = {
            "model": self.model_name,
            "query": query,
            "documents": values,
            "top_n": top_n,
        }
        try:
            import httpx
        except ImportError as exc:
            raise ConfigurationError(
                "Rerank provider requires the vssag providers extra"
            ) from exc

        client = self._http_client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(
                self.endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            body = response.json()
            rows = body.get("results")
            if not isinstance(rows, list) and isinstance(body.get("data"), dict):
                rows = body["data"].get("results")
            if not isinstance(rows, list):
                raise ProviderError(
                    f"Volcengine rerank response has no results: model={self.model_name!r}"
                )
            ranking = []
            for row in rows[:top_n]:
                if not isinstance(row, dict):
                    raise ProviderError(
                        "Volcengine rerank result item must be an object"
                    )
                index = int(row["index"])
                score = float(row["relevance_score"])
                if index < 0 or index >= len(values):
                    raise ProviderError(
                        "Volcengine rerank returned an invalid document index"
                    )
                ranking.append((index, score))
            return ranking
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "Volcengine rerank request failed: "
                f"model={self.model_name!r}, endpoint={self.endpoint!r}"
            ) from exc
        finally:
            if self._http_client is None:
                await client.aclose()
