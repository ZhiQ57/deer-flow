"""外部向量服务协议、文本构造和批量适配工具。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Protocol, runtime_checkable

from ..schemas import FieldValueRecord


class EmbeddingProviderProtocolError(TypeError):
    """外部向量服务未实现 SDK 所需调用协议时抛出的错误。"""


class EmbeddingProviderError(RuntimeError):
    """调用外部向量服务失败时抛出的错误。"""


class EmbeddingVectorError(ValueError):
    """外部向量服务返回无效向量时抛出的错误。"""


class EmbeddingDimensionError(EmbeddingVectorError):
    """embedding 向量维度与配置或索引结构不一致时抛出的错误。"""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """外部向量服务协议，SDK 只依赖此协议，不内置具体模型或厂商适配器。"""

    def embed_query(self, text: str) -> Sequence[float]:
        """生成单条查询文本向量。

        Args:
            text: 待向量化的查询文本。

        Returns:
            向量浮点数序列。
        """


@runtime_checkable
class BatchEmbeddingProvider(Protocol):
    """可选批量向量服务协议，用于索引同步或离线构建时减少外部调用。"""

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """生成多条文档文本向量。

        Args:
            texts: 待向量化文本列表。

        Returns:
            与输入顺序一致的向量列表。
        """


@dataclass(frozen=True)
class EmbeddingInput:
    """单条待向量化文本，包含用途、稳定标识和业务 metadata。"""

    kind: str
    text: str
    identifier: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验 embedding 输入文本和用途。"""
        if not self.kind.strip():
            raise ValueError("embedding input kind must not be empty")
        if not self.text.strip():
            raise ValueError("embedding input text must not be empty")


@dataclass(frozen=True)
class EmbeddingVector:
    """已生成的 embedding 向量及其对应输入。"""

    input: EmbeddingInput
    vector: tuple[float, ...]


class InMemoryEmbeddingCache:
    """轻量内存 embedding 缓存，适合单进程索引构建或测试场景。"""

    def __init__(self, max_size: int | None = 10000):
        """初始化内存缓存。

        Args:
            max_size: 最大缓存条数；为 None 时不限制，为 0 时等价于禁用缓存。

        Returns:
            None。
        """
        if max_size is not None and max_size < 0:
            raise ValueError("embedding cache max_size must be >= 0 or None")
        self.max_size = max_size
        self._vectors: dict[tuple[str, str | None, str], tuple[float, ...]] = {}

    def get(self, item: EmbeddingInput) -> tuple[float, ...] | None:
        """读取缓存向量。

        Args:
            item: embedding 输入。

        Returns:
            已缓存向量；不存在时返回 None。
        """
        return self._vectors.get(self._key(item))

    def set(self, item: EmbeddingInput, vector: Sequence[float]) -> None:
        """写入缓存向量。

        Args:
            item: embedding 输入。
            vector: 已校验的向量。

        Returns:
            None。
        """
        if self.max_size == 0:
            return
        key = self._key(item)
        self._vectors[key] = tuple(vector)
        if self.max_size is not None:
            while len(self._vectors) > self.max_size:
                oldest_key = next(iter(self._vectors))
                self._vectors.pop(oldest_key, None)

    def clear(self) -> None:
        """清空缓存。"""
        self._vectors.clear()

    def __len__(self) -> int:
        """返回当前缓存条数。"""
        return len(self._vectors)

    @staticmethod
    def _key(item: EmbeddingInput) -> tuple[str, str | None, str]:
        """构造稳定缓存 key。"""
        return item.kind, item.identifier, item.text


class EmbeddingBatchEncoder:
    """批量向量编码器，负责缓存、批量调用和外部向量边界校验。"""

    def __init__(
        self,
        provider: EmbeddingProvider | BatchEmbeddingProvider,
        *,
        expected_dimension: int | None = None,
        cache: InMemoryEmbeddingCache | None = None,
    ):
        """初始化批量编码器。

        Args:
            provider: 外部注入的 embedding provider。
            expected_dimension: 期望向量维度；为空时只校验非空、数值、有限值和非全 0。
            cache: 可选内存缓存。

        Returns:
            None。
        """
        if not _has_callable(provider, "embed_query") and not _has_callable(provider, "embed_documents"):
            raise EmbeddingProviderProtocolError(
                "embedding provider must provide callable embed_query(text) or embed_documents(texts)"
            )
        self.provider = provider
        self.expected_dimension = expected_dimension
        self.cache = cache

    def embed(self, inputs: Sequence[EmbeddingInput]) -> list[EmbeddingVector]:
        """批量生成 embedding 向量。

        Args:
            inputs: 待向量化输入列表。

        Returns:
            与输入顺序一致的向量结果。
        """
        if not inputs:
            return []

        cached: dict[int, tuple[float, ...]] = {}
        misses: list[tuple[int, EmbeddingInput]] = []
        for index, item in enumerate(inputs):
            cached_vector = self.cache.get(item) if self.cache is not None else None
            if cached_vector is None:
                misses.append((index, item))
            else:
                cached[index] = cached_vector

        if misses:
            generated = _coerce_batch_vectors(
                self._embed_missing([item for _, item in misses]),
                expected_count=len(misses),
            )
            for (index, item), vector in zip(misses, generated, strict=True):
                normalized = normalize_embedding_vector(
                    vector,
                    expected_dimension=self.expected_dimension,
                    label=f"{item.kind} embedding",
                )
                cached[index] = normalized
                if self.cache is not None:
                    self.cache.set(item, normalized)

        return [EmbeddingVector(input=item, vector=cached[index]) for index, item in enumerate(inputs)]

    def _embed_missing(self, inputs: Sequence[EmbeddingInput]) -> Sequence[Sequence[float]]:
        """调用 provider 生成未命中缓存的向量。"""
        texts = [item.text for item in inputs]
        if _has_callable(self.provider, "embed_documents"):
            embed_documents = _require_provider_method(self.provider, "embed_documents")
            try:
                return embed_documents(texts)
            except Exception as exc:
                raise EmbeddingProviderError(
                    f"embedding provider embed_documents(texts) failed for {len(texts)} inputs: {exc}"
                ) from exc
        embed_query = _require_provider_method(self.provider, "embed_query")
        vectors: list[Sequence[float]] = []
        for index, text in enumerate(texts):
            try:
                vectors.append(embed_query(text))
            except Exception as exc:
                raise EmbeddingProviderError(
                    f"embedding provider embed_query(text) failed at batch index {index}: {exc}"
                ) from exc
        return vectors


def embed_query_vector(
    provider: EmbeddingProvider,
    text: str,
    *,
    expected_dimension: int | None = None,
) -> tuple[float, ...]:
    """调用外部 provider 生成查询向量并完成 SDK 边界校验。

    Args:
        provider: 外部 embedding provider。
        text: 查询文本。
        expected_dimension: 期望向量维度。

    Returns:
        已标准化为 tuple 的向量。
    """
    if not str(text).strip():
        raise ValueError("embedding query text must not be empty")
    embed_query = _require_provider_method(provider, "embed_query")
    try:
        raw_vector = embed_query(text)
    except Exception as exc:
        raise EmbeddingProviderError(f"embedding provider embed_query(text) failed: {exc}") from exc
    return normalize_embedding_vector(
        raw_vector,
        expected_dimension=expected_dimension,
        label="query embedding",
    )


def normalize_embedding_vector(
    values: Iterable[float],
    *,
    expected_dimension: int | None = None,
    label: str = "embedding vector",
) -> tuple[float, ...]:
    """校验并标准化外部 provider 返回的向量。

    Args:
        values: 外部 provider 返回的向量。
        expected_dimension: 期望向量维度。
        label: 错误信息中的向量名称。

    Returns:
        标准化后的浮点 tuple。
    """
    if expected_dimension is not None and expected_dimension <= 0:
        raise ValueError(f"{label} expected_dimension must be a positive integer or None")
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        raise TypeError(f"{label} must be a non-empty numeric sequence")
    try:
        value_list = list(values)
    except TypeError as exc:
        raise TypeError(f"{label} must be a non-empty numeric sequence") from exc
    if not value_list:
        raise EmbeddingVectorError(f"{label} must not be empty")
    if expected_dimension is not None and len(value_list) != expected_dimension:
        raise EmbeddingDimensionError(
            f"{label} dimension mismatch: expected {expected_dimension}, got {len(value_list)}"
        )

    normalized: list[float] = []
    for index, value in enumerate(value_list):
        if isinstance(value, bool):
            raise TypeError(f"{label} item at index {index} must be numeric, got bool")
        try:
            item = float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{label} item at index {index} is not numeric: {value!r}") from exc
        if not isfinite(item):
            raise EmbeddingVectorError(f"{label} item at index {index} must be finite: {value!r}")
        normalized.append(item)
    if all(item == 0.0 for item in normalized):
        raise EmbeddingVectorError(f"{label} must not be an all-zero vector")
    return tuple(normalized)


def _has_callable(provider: object, method_name: str) -> bool:
    """判断 provider 是否提供可调用方法。"""
    return callable(getattr(provider, method_name, None))


def _require_provider_method(provider: object, method_name: str):
    """读取并校验外部向量服务方法。

    Args:
        provider: 外部业务项目注入的向量服务对象。
        method_name: 需要的方法名，例如 `embed_query`。

    Returns:
        可调用方法。
    """
    method = getattr(provider, method_name, None)
    if not callable(method):
        raise EmbeddingProviderProtocolError(
            f"embedding provider must provide callable {method_name}"
        )
    return method


def _coerce_batch_vectors(
    vectors: object,
    *,
    expected_count: int,
) -> list[Sequence[float]]:
    """校验批量 provider 返回结构并转换为列表。

    Args:
        vectors: 外部批量 provider 返回值。
        expected_count: 本次请求的输入数量。

    Returns:
        与输入顺序一致的向量列表。
    """
    if isinstance(vectors, (str, bytes, Mapping)) or not isinstance(vectors, Iterable):
        raise EmbeddingProviderError(
            "embedding provider embed_documents(texts) must return a sequence of vectors"
        )
    vector_list = list(vectors)
    if len(vector_list) != expected_count:
        raise EmbeddingProviderError(
            f"embedding provider returned {len(vector_list)} vectors for {expected_count} inputs"
        )
    return vector_list


def build_table_embedding_text(
    *,
    table_name: str,
    table_label: str | None = None,
    table_describe: str | None = None,
    table_entities: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """构造表级 embedding 文本。

    Args:
        table_name: 表名。
        table_label: 表中文或业务标签。
        table_describe: 表业务描述。
        table_entities: 表相关实体、别名或关键词。
        metadata: 表级业务 metadata。

    Returns:
        稳定、可解释的表级 embedding 文本。
    """
    return _join_embedding_parts(
        [
            ("对象类型", "table"),
            ("表名", table_name),
            ("表标签", table_label),
            ("业务定义", table_describe),
            ("关键实体", _join_values(table_entities)),
            ("metadata", _metadata_summary(metadata)),
        ]
    )


def build_column_embedding_text(
    *,
    table_name: str,
    column_name: str,
    column_comment: str | None = None,
    column_entities: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
    table_label: str | None = None,
) -> str:
    """构造列级 embedding 文本。

    Args:
        table_name: 所属表名。
        column_name: 列名。
        column_comment: 列注释或业务说明。
        column_entities: 列相关实体、别名或关键词。
        metadata: 列级业务 metadata。
        table_label: 可选表业务标签。

    Returns:
        稳定、可解释的列级 embedding 文本。
    """
    return _join_embedding_parts(
        [
            ("对象类型", "column"),
            ("所属表", table_name),
            ("表标签", table_label),
            ("列名", column_name),
            ("列说明", column_comment),
            ("列实体", _join_values(column_entities)),
            ("metadata", _metadata_summary(metadata)),
        ]
    )


def build_value_embedding_text(
    *,
    table_name: str,
    column_name: str,
    raw_value: str,
    aliases: Sequence[str] | None = None,
    normalized_value: str | None = None,
    table_comment: str | None = None,
    column_comment: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """构造字段值级 embedding 文本。

    Args:
        table_name: 逻辑表名。
        column_name: 逻辑列名。
        raw_value: 数据库真实字段值。
        aliases: 字段值别名。
        normalized_value: 归一化字段值。
        table_comment: 表业务说明。
        column_comment: 列业务说明。
        metadata: 字段值 metadata。

    Returns:
        稳定、可解释的字段值级 embedding 文本。
    """
    return _join_embedding_parts(
        [
            ("对象类型", "value"),
            ("表名", table_name),
            ("表说明", table_comment),
            ("列名", column_name),
            ("列说明", column_comment),
            ("原始值", raw_value),
            ("归一化值", normalized_value),
            ("别名", _join_values(aliases)),
            ("metadata", _metadata_summary(metadata)),
        ]
    )


def build_field_value_embedding_text(record: FieldValueRecord) -> str:
    """从字段值索引记录构造 embedding 文本。

    Args:
        record: 字段值索引记录。

    Returns:
        字段值级 embedding 文本。
    """
    return build_value_embedding_text(
        table_name=record.table_name,
        table_comment=record.table_comment,
        column_name=record.column_name,
        column_comment=record.column_comment,
        raw_value=record.raw_value,
        aliases=record.aliases,
        normalized_value=record.normalized_value,
        metadata=record.metadata,
    )


def table_embedding_input(
    *,
    table_name: str,
    table_label: str | None = None,
    table_describe: str | None = None,
    table_entities: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> EmbeddingInput:
    """构造表级 embedding 输入。"""
    return EmbeddingInput(
        kind="table",
        identifier=table_name,
        text=build_table_embedding_text(
            table_name=table_name,
            table_label=table_label,
            table_describe=table_describe,
            table_entities=table_entities,
            metadata=metadata,
        ),
        metadata=dict(metadata or {}),
    )


def column_embedding_input(
    *,
    table_name: str,
    column_name: str,
    column_comment: str | None = None,
    column_entities: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
    table_label: str | None = None,
) -> EmbeddingInput:
    """构造列级 embedding 输入。"""
    return EmbeddingInput(
        kind="column",
        identifier=f"{table_name}.{column_name}",
        text=build_column_embedding_text(
            table_name=table_name,
            table_label=table_label,
            column_name=column_name,
            column_comment=column_comment,
            column_entities=column_entities,
            metadata=metadata,
        ),
        metadata=dict(metadata or {}),
    )


def value_embedding_input(record: FieldValueRecord) -> EmbeddingInput:
    """构造字段值级 embedding 输入。"""
    return EmbeddingInput(
        kind="value",
        identifier=f"{record.table_name}.{record.column_name}:{record.raw_value}",
        text=build_field_value_embedding_text(record),
        metadata=dict(record.metadata),
    )


def _join_embedding_parts(parts: Sequence[tuple[str, str | None]]) -> str:
    """按固定标签拼接 embedding 文本。"""
    return "\n".join(f"{label}: {value}" for label, value in parts if value and str(value).strip())


def _join_values(values: Sequence[str] | None) -> str | None:
    """把字符串序列拼成稳定文本。"""
    if not values:
        return None
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return " | ".join(dict.fromkeys(cleaned)) if cleaned else None


def _metadata_summary(metadata: dict[str, Any] | None) -> str | None:
    """提取适合进入 embedding 文本的 metadata 摘要。"""
    if not metadata:
        return None
    priority_keys = [
        "business_domain",
        "domain",
        "grain",
        "granularity",
        "role",
        "roles",
        "aliases",
        "business_aliases",
        "common_questions",
        "metrics",
        "dimensions",
        "aggregation",
        "value_examples",
        "tags",
    ]
    parts: list[str] = []
    seen: set[str] = set()
    for key in priority_keys:
        if key in metadata:
            text = _stringify_metadata_value(metadata[key])
            if text:
                parts.append(f"{key}={text}")
                seen.add(key)
    for key in sorted(metadata):
        if key in seen:
            continue
        text = _stringify_metadata_value(metadata[key])
        if text:
            parts.append(f"{key}={text}")
        if len(parts) >= 16:
            break
    return " | ".join(parts) if parts else None


def _stringify_metadata_value(value: Any) -> str | None:
    """把 metadata 值转换为短文本，避免过长或空值污染 embedding。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, dict):
        nested = [
            f"{key}:{_stringify_metadata_value(item)}"
            for key, item in sorted(value.items())
            if _stringify_metadata_value(item)
        ]
        text = ", ".join(nested)
    elif isinstance(value, (list, tuple, set)):
        items = [_stringify_metadata_value(item) for item in value]
        text = ", ".join(item for item in items if item)
    else:
        text = str(value).strip()
    if not text:
        return None
    return text[:500]


__all__ = [
    "BatchEmbeddingProvider",
    "EmbeddingBatchEncoder",
    "EmbeddingDimensionError",
    "EmbeddingProviderError",
    "EmbeddingProviderProtocolError",
    "EmbeddingInput",
    "EmbeddingProvider",
    "EmbeddingVectorError",
    "EmbeddingVector",
    "InMemoryEmbeddingCache",
    "build_column_embedding_text",
    "build_field_value_embedding_text",
    "build_table_embedding_text",
    "build_value_embedding_text",
    "column_embedding_input",
    "embed_query_vector",
    "normalize_embedding_vector",
    "table_embedding_input",
    "value_embedding_input",
]
