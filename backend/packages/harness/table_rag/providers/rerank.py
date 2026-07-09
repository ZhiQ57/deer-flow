"""外部重排序服务协议和调用边界校验。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Protocol, TypeAlias, runtime_checkable


class RerankProviderProtocolError(TypeError):
    """外部重排序服务未实现 SDK 所需调用协议时抛出的错误。"""


class RerankProviderError(RuntimeError):
    """调用外部重排序服务失败时抛出的错误。"""


class RerankScoreError(ValueError):
    """外部重排序服务返回非法分数时抛出的错误。"""


@dataclass(frozen=True)
class RerankDocument:
    """单条重排序候选文档。"""

    kind: str
    identifier: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验重排序候选文档的基础字段。"""
        if not str(self.kind).strip():
            raise ValueError("rerank document kind must not be empty")
        if not str(self.identifier).strip():
            raise ValueError("rerank document identifier must not be empty")
        if not str(self.text).strip():
            raise ValueError("rerank document text must not be empty")


@dataclass(frozen=True)
class RerankResult:
    """外部重排序服务对单条候选文档返回的标准结果。"""

    document: RerankDocument
    score: float


@runtime_checkable
class RerankProvider(Protocol):
    """外部重排序服务同步单条打分协议，SDK 不内置具体模型或厂商适配器。"""

    def score(self, query: str, document: RerankDocument) -> float:
        """给单条候选文档打相关性分数。

        Args:
            query: 用户问题。
            document: 待重排序候选文档。

        Returns:
            外部模型返回的相关性分数。
        """


@runtime_checkable
class BatchRerankProvider(Protocol):
    """外部重排序服务同步批量打分协议，用于减少模型服务调用次数。"""

    def score_batch(self, query: str, documents: Sequence[RerankDocument]) -> Sequence[float]:
        """给多条候选文档批量打相关性分数。

        Args:
            query: 用户问题。
            documents: 待重排序候选文档列表。

        Returns:
            与输入顺序一致的相关性分数列表。
        """


@runtime_checkable
class AsyncRerankProvider(Protocol):
    """外部重排序服务异步单条打分协议，供异步检索链路按需实现。"""

    async def ascore(self, query: str, document: RerankDocument) -> float:
        """异步给单条候选文档打相关性分数。

        Args:
            query: 用户问题。
            document: 待重排序候选文档。

        Returns:
            外部模型返回的相关性分数。
        """


@runtime_checkable
class AsyncBatchRerankProvider(Protocol):
    """外部重排序服务异步批量打分协议，供异步检索链路按需实现。"""

    async def ascore_batch(self, query: str, documents: Sequence[RerankDocument]) -> Sequence[float]:
        """异步给多条候选文档批量打相关性分数。

        Args:
            query: 用户问题。
            documents: 待重排序候选文档列表。

        Returns:
            与输入顺序一致的相关性分数列表。
        """


RerankProviderLike: TypeAlias = RerankProvider | BatchRerankProvider
"""同步重排序 Provider 联合类型。"""

AsyncRerankProviderLike: TypeAlias = AsyncRerankProvider | AsyncBatchRerankProvider
"""异步重排序 Provider 联合类型。"""


def rerank_document(
    provider: RerankProviderLike,
    query: str,
    document: RerankDocument,
) -> RerankResult:
    """调用同步重排序 provider 给单条候选文档打分。

    Args:
        provider: 外部注入的同步重排序 provider。
        query: 用户问题。
        document: 待重排序候选文档。

    Returns:
        标准化后的重排序结果。
    """
    return rerank_documents(provider, query, [document])[0]


def rerank_documents(
    provider: RerankProviderLike,
    query: str,
    documents: Sequence[RerankDocument],
) -> list[RerankResult]:
    """调用同步重排序 provider 给多条候选文档打分。

    Args:
        provider: 外部注入的同步重排序 provider。
        query: 用户问题。
        documents: 待重排序候选文档列表。

    Returns:
        与输入顺序一致的标准重排序结果。
    """
    clean_query = _normalize_query(query)
    document_list = _coerce_documents(documents)
    if not document_list:
        return []

    if _has_callable(provider, "score_batch"):
        score_batch = _require_provider_method(provider, "score_batch")
        try:
            raw_scores = score_batch(clean_query, document_list)
        except Exception as exc:
            raise RerankProviderError(
                f"rerank provider score_batch(query, documents) failed for {len(document_list)} documents: {exc}"
            ) from exc
    elif _has_callable(provider, "score"):
        score = _require_provider_method(provider, "score")
        raw_scores = []
        for index, document in enumerate(document_list):
            try:
                raw_scores.append(score(clean_query, document))
            except Exception as exc:
                raise RerankProviderError(
                    f"rerank provider score(query, document) failed at batch index {index}: {exc}"
                ) from exc
    else:
        raise RerankProviderProtocolError(
            "rerank provider must provide callable score(query, document) or score_batch(query, documents)"
        )

    scores = _coerce_scores(raw_scores, expected_count=len(document_list))
    return [
        RerankResult(
            document=document,
            score=normalize_rerank_score(value, label=f"rerank score at index {index}"),
        )
        for index, (document, value) in enumerate(zip(document_list, scores, strict=True))
    ]


async def async_rerank_document(
    provider: AsyncRerankProviderLike,
    query: str,
    document: RerankDocument,
) -> RerankResult:
    """调用异步重排序 provider 给单条候选文档打分。

    Args:
        provider: 外部注入的异步重排序 provider。
        query: 用户问题。
        document: 待重排序候选文档。

    Returns:
        标准化后的重排序结果。
    """
    return (await async_rerank_documents(provider, query, [document]))[0]


async def async_rerank_documents(
    provider: AsyncRerankProviderLike,
    query: str,
    documents: Sequence[RerankDocument],
) -> list[RerankResult]:
    """调用异步重排序 provider 给多条候选文档打分。

    Args:
        provider: 外部注入的异步重排序 provider。
        query: 用户问题。
        documents: 待重排序候选文档列表。

    Returns:
        与输入顺序一致的标准重排序结果。
    """
    clean_query = _normalize_query(query)
    document_list = _coerce_documents(documents)
    if not document_list:
        return []

    if _has_callable(provider, "ascore_batch"):
        ascore_batch = _require_provider_method(provider, "ascore_batch")
        try:
            raw_scores = await ascore_batch(clean_query, document_list)
        except Exception as exc:
            raise RerankProviderError(
                f"rerank provider ascore_batch(query, documents) failed for {len(document_list)} documents: {exc}"
            ) from exc
    elif _has_callable(provider, "ascore"):
        ascore = _require_provider_method(provider, "ascore")
        raw_scores = []
        for index, document in enumerate(document_list):
            try:
                raw_scores.append(await ascore(clean_query, document))
            except Exception as exc:
                raise RerankProviderError(
                    f"rerank provider ascore(query, document) failed at batch index {index}: {exc}"
                ) from exc
    else:
        raise RerankProviderProtocolError(
            "async rerank provider must provide callable ascore(query, document) "
            "or ascore_batch(query, documents)"
        )

    scores = _coerce_scores(raw_scores, expected_count=len(document_list))
    return [
        RerankResult(
            document=document,
            score=normalize_rerank_score(value, label=f"rerank score at index {index}"),
        )
        for index, (document, value) in enumerate(zip(document_list, scores, strict=True))
    ]


def normalize_rerank_score(value: object, *, label: str = "rerank score") -> float:
    """校验并标准化外部 provider 返回的重排序分数。

    Args:
        value: 外部 provider 返回的原始分数。
        label: 错误信息中的分数名称。

    Returns:
        标准化后的浮点分数。
    """
    if isinstance(value, bool):
        raise TypeError(f"{label} must be numeric, got bool")
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{label} must be numeric, got {value!r}") from exc
    if not isfinite(score):
        raise RerankScoreError(f"{label} must be finite, got {value!r}")
    return score


def _normalize_query(query: str) -> str:
    """校验用户问题并转换为字符串。"""
    clean_query = str(query).strip()
    if not clean_query:
        raise ValueError("rerank query must not be empty")
    return clean_query


def _coerce_documents(documents: Sequence[RerankDocument]) -> list[RerankDocument]:
    """校验批量候选文档结构并转换为列表。"""
    if isinstance(documents, (str, bytes, Mapping)) or not isinstance(documents, Iterable):
        raise TypeError("rerank documents must be a sequence of RerankDocument")
    document_list = list(documents)
    for index, document in enumerate(document_list):
        if not isinstance(document, RerankDocument):
            raise TypeError(
                f"rerank documents item at index {index} must be RerankDocument, "
                f"got {type(document).__name__}"
            )
    return document_list


def _coerce_scores(scores: object, *, expected_count: int) -> list[object]:
    """校验批量 provider 返回结构并转换为列表。"""
    if isinstance(scores, (str, bytes, Mapping)) or not isinstance(scores, Iterable):
        raise RerankProviderError("rerank provider batch method must return a sequence of scores")
    score_list = list(scores)
    if len(score_list) != expected_count:
        raise RerankProviderError(
            f"rerank provider returned {len(score_list)} scores for {expected_count} documents"
        )
    return score_list


def _has_callable(provider: object, method_name: str) -> bool:
    """判断 provider 是否提供可调用方法。"""
    return callable(getattr(provider, method_name, None))


def _require_provider_method(provider: object, method_name: str):
    """读取并校验外部重排序服务方法。"""
    method = getattr(provider, method_name, None)
    if not callable(method):
        raise RerankProviderProtocolError(f"rerank provider must provide callable {method_name}")
    return method


__all__ = [
    "AsyncBatchRerankProvider",
    "AsyncRerankProvider",
    "AsyncRerankProviderLike",
    "BatchRerankProvider",
    "RerankDocument",
    "RerankProvider",
    "RerankProviderError",
    "RerankProviderLike",
    "RerankProviderProtocolError",
    "RerankResult",
    "RerankScoreError",
    "async_rerank_document",
    "async_rerank_documents",
    "normalize_rerank_score",
    "rerank_document",
    "rerank_documents",
]
