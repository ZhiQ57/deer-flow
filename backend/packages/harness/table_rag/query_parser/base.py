"""查询解析和扩展基础抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace

from ..schemas import ParsedQuery, QueryExpansion


class QueryParserBase(ABC):
    """查询解析器抽象类，用于把自然语言问题解析成检索提示信号。"""

    @abstractmethod
    def parse(self, query: str) -> ParsedQuery:
        """解析用户问题。

        Args:
            query: 用户自然语言问题。

        Returns:
            结构化查询理解结果。
        """


class QueryExpansionProvider(ABC):
    """查询扩展提供器抽象类，用于按 TableRAG 约束补充检索关键词。"""

    @abstractmethod
    def expand(self, query: str, parsed_query: ParsedQuery | None = None) -> QueryExpansion:
        """扩展用户查询。

        Args:
            query: 用户自然语言问题。
            parsed_query: 可选的轻量解析结果，便于外部实现复用已有信号。

        Returns:
            查询扩展结果。
        """


class EmptyQueryExpansionProvider(QueryExpansionProvider):
    """空查询扩展提供器，表示不使用额外扩展能力。"""

    def expand(self, query: str, parsed_query: ParsedQuery | None = None) -> QueryExpansion:
        """返回空扩展结果。"""
        return QueryExpansion()


def merge_query_expansion(parsed_query: ParsedQuery, expansion: QueryExpansion) -> ParsedQuery:
    """把查询扩展结果合并到解析结果。

    Args:
        parsed_query: 原始查询解析结果。
        expansion: 外部扩展结果。

    Returns:
        合并后的查询解析结果。
    """
    return replace(
        parsed_query,
        metrics=_dedupe([*parsed_query.metrics, *expansion.metrics]),
        dimensions=_dedupe([*parsed_query.dimensions, *expansion.dimensions]),
        entities=_dedupe([*parsed_query.entities, *expansion.entities]),
        filters=_dedupe([*parsed_query.filters, *expansion.filters]),
        time_expressions=_dedupe([*parsed_query.time_expressions, *expansion.time_expressions]),
        expanded_terms=_dedupe([*parsed_query.expanded_terms, *expansion.expanded_terms]),
    )


def _dedupe(values: list[str]) -> list[str]:
    """按原始顺序去重并清理空字符串。"""
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


__all__ = [
    "EmptyQueryExpansionProvider",
    "QueryExpansionProvider",
    "QueryParserBase",
    "merge_query_expansion",
]
