"""基于外部重排序 Provider 的 Schema 精排适配器。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ...providers.rerank import (
    AsyncRerankProviderLike,
    RerankDocument,
    RerankProviderLike,
    RerankProviderProtocolError,
    async_rerank_documents,
    rerank_documents,
)
from ...schemas import ColumnRetrievalResult, TableRetrievalResult, ValueRetrievalResult
from .base import AsyncRetrievalFineRankerBase, RetrievalFineRankerBase


class ProviderSchemaFineRanker(RetrievalFineRankerBase, AsyncRetrievalFineRankerBase):
    """外部重排序 Provider 精排适配器，用模型分数重排表和字段候选。"""

    def __init__(self, provider: RerankProviderLike | AsyncRerankProviderLike):
        """初始化外部精排适配器。

        Args:
            provider: 用户项目外部实现并注入的重排序 provider。

        Returns:
            None。
        """
        if not any(
            callable(getattr(provider, method_name, None))
            for method_name in ("score", "score_batch", "ascore", "ascore_batch")
        ):
            raise RerankProviderProtocolError(
                "rerank provider must provide score, score_batch, ascore or ascore_batch"
            )
        self.provider = provider

    def rerank_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """调用同步 provider 精排候选表。

        Args:
            query: 用户问题。
            tables: 表候选。
            columns: 字段候选，用于补充表级精排上下文。
            values: 字段值候选，用于补充表级精排上下文。

        Returns:
            精排后的表候选。
        """
        table_list = list(tables)
        if not table_list:
            return []
        documents = [
            build_table_rerank_document(table, columns=columns, values=values)
            for table in table_list
        ]
        results = rerank_documents(self.provider, query, documents)
        return _sort_tables_with_scores(table_list, [result.score for result in results])

    def rerank_columns(self, query: str, columns: Sequence[ColumnRetrievalResult]) -> list[ColumnRetrievalResult]:
        """调用同步 provider 精排候选字段。

        Args:
            query: 用户问题。
            columns: 字段候选。

        Returns:
            精排后的字段候选。
        """
        column_list = list(columns)
        if not column_list:
            return []
        documents = [build_column_rerank_document(column) for column in column_list]
        results = rerank_documents(self.provider, query, documents)
        return _sort_columns_with_scores(column_list, [result.score for result in results])

    async def arerank_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """调用异步 provider 精排候选表。

        Args:
            query: 用户问题。
            tables: 表候选。
            columns: 字段候选，用于补充表级精排上下文。
            values: 字段值候选，用于补充表级精排上下文。

        Returns:
            精排后的表候选。
        """
        table_list = list(tables)
        if not table_list:
            return []
        documents = [
            build_table_rerank_document(table, columns=columns, values=values)
            for table in table_list
        ]
        results = await async_rerank_documents(self.provider, query, documents)
        return _sort_tables_with_scores(table_list, [result.score for result in results])

    async def arerank_columns(
        self,
        query: str,
        columns: Sequence[ColumnRetrievalResult],
    ) -> list[ColumnRetrievalResult]:
        """调用异步 provider 精排候选字段。

        Args:
            query: 用户问题。
            columns: 字段候选。

        Returns:
            精排后的字段候选。
        """
        column_list = list(columns)
        if not column_list:
            return []
        documents = [build_column_rerank_document(column) for column in column_list]
        results = await async_rerank_documents(self.provider, query, documents)
        return _sort_columns_with_scores(column_list, [result.score for result in results])


def build_table_rerank_document(
    table: TableRetrievalResult,
    *,
    columns: Sequence[ColumnRetrievalResult] = (),
    values: Sequence[ValueRetrievalResult] = (),
) -> RerankDocument:
    """把表候选转换为外部重排序候选文档。

    Args:
        table: 表候选。
        columns: 同表字段候选上下文。
        values: 同表字段值候选上下文。

    Returns:
        可传给外部重排序 provider 的候选文档。
    """
    table_columns = [column for column in columns if column.table_name == table.table_name]
    table_values = [value for value in values if value.table_name == table.table_name]
    text = _join_text_parts(
        [
            ("对象类型", "table"),
            ("表名", table.table_name),
            ("表标签", table.table_label),
            ("表说明", table.table_describe),
            ("表实体", _join_values(table.table_entities)),
            ("命中字段", _column_context(table_columns)),
            ("命中字段值", _value_context(table_values)),
            ("metadata", _metadata_summary(table.metadata)),
        ]
    )
    return RerankDocument(
        kind="table",
        identifier=table.table_name,
        text=text,
        metadata={
            "table_name": table.table_name,
            "source_score": table.score,
            "source_scores": dict(table.source_scores),
            "metadata": dict(table.metadata),
        },
    )


def build_column_rerank_document(column: ColumnRetrievalResult) -> RerankDocument:
    """把字段候选转换为外部重排序候选文档。

    Args:
        column: 字段候选。

    Returns:
        可传给外部重排序 provider 的候选文档。
    """
    text = _join_text_parts(
        [
            ("对象类型", "column"),
            ("所属表", column.table_name),
            ("字段名", column.column_name),
            ("字段说明", column.column_comment),
            ("字段实体", _join_values(column.column_entities)),
            ("metadata", _metadata_summary(column.metadata)),
        ]
    )
    return RerankDocument(
        kind="column",
        identifier=f"{column.table_name}.{column.column_name}",
        text=text,
        metadata={
            "table_name": column.table_name,
            "column_name": column.column_name,
            "source_score": column.score,
            "source_scores": dict(column.source_scores),
            "metadata": dict(column.metadata),
        },
    )


def _sort_tables_with_scores(
    tables: Sequence[TableRetrievalResult],
    scores: Sequence[float],
) -> list[TableRetrievalResult]:
    """把外部精排分数写回表候选并按分数降序排序。"""
    reranked = []
    for table, score in zip(tables, scores, strict=True):
        source_scores = {
            **table.source_scores,
            "pre_fine_rank": table.score,
            "external_rerank": score,
            "rerank": score,
        }
        metadata = {**table.metadata, "fine_ranker": "provider"}
        reranked.append(replace(table, score=score, source_scores=source_scores, metadata=metadata))
    return sorted(reranked, key=lambda item: item.score, reverse=True)


def _sort_columns_with_scores(
    columns: Sequence[ColumnRetrievalResult],
    scores: Sequence[float],
) -> list[ColumnRetrievalResult]:
    """把外部精排分数写回字段候选并按分数降序排序。"""
    reranked = []
    for column, score in zip(columns, scores, strict=True):
        source_scores = {
            **column.source_scores,
            "pre_fine_rank": column.score,
            "external_rerank": score,
            "rerank": score,
        }
        metadata = {**column.metadata, "fine_ranker": "provider"}
        reranked.append(replace(column, score=score, source_scores=source_scores, metadata=metadata))
    return sorted(reranked, key=lambda item: item.score, reverse=True)


def _column_context(columns: Sequence[ColumnRetrievalResult]) -> str | None:
    """构造表级精排用字段上下文。"""
    items = [
        _join_text_parts(
            [
                ("字段名", column.column_name),
                ("说明", column.column_comment),
                ("候选分", str(column.score)),
            ],
            separator=", ",
        )
        for column in columns[:8]
    ]
    return " | ".join(item for item in items if item) or None


def _value_context(values: Sequence[ValueRetrievalResult]) -> str | None:
    """构造表级精排用字段值上下文。"""
    items = [
        _join_text_parts(
            [
                ("字段名", value.column_name),
                ("字段值", value.raw_value),
                ("别名", _join_values(value.aliases)),
                ("候选分", str(value.score)),
            ],
            separator=", ",
        )
        for value in values[:8]
    ]
    return " | ".join(item for item in items if item) or None


def _join_text_parts(parts: Sequence[tuple[str, str | None]], *, separator: str = "\n") -> str:
    """把非空字段按固定标签拼接成精排文本。"""
    return separator.join(f"{label}: {value}" for label, value in parts if value and str(value).strip())


def _join_values(values: Sequence[str] | None) -> str | None:
    """把字符串序列拼成稳定文本。"""
    if not values:
        return None
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return " | ".join(dict.fromkeys(cleaned)) if cleaned else None


def _metadata_summary(metadata: dict[str, Any] | None) -> str | None:
    """把 metadata 转换成适合外部重排序模型阅读的短文本。"""
    if not metadata:
        return None
    parts: list[str] = []
    for key in sorted(metadata):
        text = _stringify_metadata_value(metadata[key])
        if text:
            parts.append(f"{key}={text}")
        if len(parts) >= 16:
            break
    return " | ".join(parts) if parts else None


def _stringify_metadata_value(value: Any) -> str | None:
    """把 metadata 值转换为短文本。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, dict):
        text = ", ".join(
            f"{key}:{_stringify_metadata_value(item)}"
            for key, item in sorted(value.items())
            if _stringify_metadata_value(item)
        )
    elif isinstance(value, (list, tuple, set)):
        values = [_stringify_metadata_value(item) for item in value]
        text = ", ".join(item for item in values if item)
    else:
        text = str(value).strip()
    return text[:500] if text else None


__all__ = [
    "ProviderSchemaFineRanker",
    "build_column_rerank_document",
    "build_table_rerank_document",
]
