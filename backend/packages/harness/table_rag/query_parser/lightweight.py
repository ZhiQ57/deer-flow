"""不依赖大模型的轻量查询解析实现。"""

from __future__ import annotations

import re

from ..providers import BusinessGlossaryProvider, InMemoryBusinessGlossaryProvider
from ..schemas import ParsedQuery, QueryExpansion
from ..utils.text import simple_text_normalize
from .base import QueryExpansionProvider, QueryParserBase, merge_query_expansion


class DefaultQueryParser(QueryParserBase):
    """默认轻量查询解析器，不依赖外部 LLM 或第三方分词服务。"""

    def __init__(
        self,
        glossary_provider: BusinessGlossaryProvider | None = None,
        expansion_provider: QueryExpansionProvider | None = None,
    ):
        """初始化默认查询解析器。

        Args:
            glossary_provider: 可选业务词库提供器。
            expansion_provider: 可选查询扩展提供器。

        Returns:
            None。
        """
        self.glossary_provider = glossary_provider or InMemoryBusinessGlossaryProvider()
        self.expansion_provider = expansion_provider

    def parse(self, query: str) -> ParsedQuery:
        """解析用户问题。"""
        normalized = simple_text_normalize(query)
        metrics: list[str] = []
        dimensions: list[str] = []
        entities: list[str] = []
        filters: list[str] = []
        time_expressions: list[str] = []
        expanded_terms: list[str] = []

        for entry in self.glossary_provider.entries():
            names = [entry.canonical, *entry.aliases]
            if not _contains_any(normalized, names):
                continue
            if entry.category == "metric":
                metrics.append(entry.canonical)
            elif entry.category == "dimension":
                dimensions.append(entry.canonical)
            elif entry.category == "entity":
                entities.append(entry.canonical)
            elif entry.category == "status":
                filters.append(entry.canonical)
            elif entry.category == "time":
                time_expressions.append(entry.canonical)
            expanded_terms.extend(names)

        top_k = _parse_top_k(query)
        sort_direction = _parse_sort_direction(query)
        time_expressions.extend(_parse_literal_time_expressions(query))

        parsed_query = ParsedQuery(
            original_text=query,
            normalized_text=normalized,
            intent=_infer_intent(metrics, time_expressions, top_k, sort_direction),
            metrics=_dedupe(metrics),
            dimensions=_dedupe(dimensions),
            entities=_dedupe(entities),
            filters=_dedupe(filters),
            time_expressions=_dedupe(time_expressions),
            expanded_terms=_dedupe(expanded_terms),
            top_k=top_k,
            sort_direction=sort_direction,
        )
        if self.expansion_provider is None:
            return parsed_query
        expansion = self.expansion_provider.expand(query, parsed_query)
        return merge_query_expansion(parsed_query, expansion)


class SimpleTokenQueryExpansionProvider(QueryExpansionProvider):
    """基于正则的轻量关键词扩展提供器，不依赖第三方分词库。"""

    def __init__(self, *, min_token_length: int = 2, max_terms: int = 32):
        """初始化正则分词扩展器。

        Args:
            min_token_length: 最短关键词长度。
            max_terms: 最大返回关键词数量。

        Returns:
            None。
        """
        if min_token_length <= 0:
            raise ValueError("min_token_length must be positive")
        if max_terms <= 0:
            raise ValueError("max_terms must be positive")
        self.min_token_length = min_token_length
        self.max_terms = max_terms

    def expand(self, query: str, parsed_query: ParsedQuery | None = None) -> QueryExpansion:
        """从查询文本抽取轻量关键词。"""
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]+", query)
        keywords = [
            token
            for token in tokens
            if len(token.strip()) >= self.min_token_length
        ]
        return QueryExpansion(keywords=_dedupe(keywords)[: self.max_terms])


class JiebaQueryExpansionProvider(QueryExpansionProvider):
    """基于可选 jieba 依赖的中文关键词扩展提供器。"""

    def __init__(self, *, min_token_length: int = 2, max_terms: int = 32):
        """初始化 jieba 分词扩展器。

        Args:
            min_token_length: 最短关键词长度。
            max_terms: 最大返回关键词数量。

        Returns:
            None。
        """
        if min_token_length <= 0:
            raise ValueError("min_token_length must be positive")
        if max_terms <= 0:
            raise ValueError("max_terms must be positive")
        self.min_token_length = min_token_length
        self.max_terms = max_terms

    def expand(self, query: str, parsed_query: ParsedQuery | None = None) -> QueryExpansion:
        """调用 jieba 对查询做分词扩展。"""
        try:
            import jieba
        except ImportError as exc:
            raise RuntimeError("jieba is required for JiebaQueryExpansionProvider") from exc
        keywords = [
            token.strip()
            for token in jieba.lcut(query)
            if len(token.strip()) >= self.min_token_length
        ]
        return QueryExpansion(keywords=_dedupe(keywords)[: self.max_terms])


def _contains_any(normalized_query: str, names: list[str]) -> bool:
    """判断查询是否包含任一词条表达。"""
    return any(simple_text_normalize(name) in normalized_query for name in names if name)


def _parse_top_k(query: str) -> int | None:
    """解析 TopN / 前 N 名表达。"""
    patterns = [
        r"(?:top|TOP)\s*(\d{1,3})",
        r"前\s*(\d{1,3})\s*(?:名|个|家|条|款)?",
        r"排名\s*前\s*(\d{1,3})",
    ]
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            return int(match.group(1))
    return None


def _parse_sort_direction(query: str) -> str | None:
    """解析排序方向。"""
    if re.search(r"最高|最多|最大|top|TOP|前\s*\d+", query):
        return "desc"
    if re.search(r"最低|最少|最小", query):
        return "asc"
    return None


def _parse_literal_time_expressions(query: str) -> list[str]:
    """抽取字面时间表达。"""
    patterns = [
        r"\d{4}\s*年\s*\d{1,2}\s*月",
        r"\d{4}\s*年",
        r"\d{1,2}\s*月",
        r"最近\s*\d+\s*(?:天|周|个月|月|年)",
        r"近\s*\d+\s*(?:天|周|个月|月|年)",
        r"本周|上周|本月|上月|今年|去年|今日|昨天|昨日",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(match.group(0) for match in re.finditer(pattern, query))
    return found


def _infer_intent(
    metrics: list[str],
    time_expressions: list[str],
    top_k: int | None,
    sort_direction: str | None,
) -> str:
    """根据解析信号推断查询意图。"""
    if any(item in {"同比", "环比"} for item in time_expressions):
        return "time_comparison"
    if top_k is not None or sort_direction is not None:
        return "ranking"
    if metrics:
        return "aggregation"
    return "lookup"


def _dedupe(values: list[str]) -> list[str]:
    """按原始顺序去重。"""
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "DefaultQueryParser",
    "JiebaQueryExpansionProvider",
    "SimpleTokenQueryExpansionProvider",
]
