"""查询理解相关标准数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QueryExpansion:
    """查询扩展结果，用于把外部解析能力转成 TableRAG 统一检索提示。"""

    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    time_expressions: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def expanded_terms(self) -> list[str]:
        """生成可直接合并到 schema 检索文本的扩展词。"""
        parts = [
            *self.metrics,
            *self.dimensions,
            *self.entities,
            *self.filters,
            *self.time_expressions,
            *self.keywords,
        ]
        return list(dict.fromkeys(part for part in parts if part))


@dataclass(frozen=True)
class ParsedQuery:
    """轻量查询解析结果。"""

    original_text: str
    normalized_text: str
    intent: str = "lookup"
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    time_expressions: list[str] = field(default_factory=list)
    expanded_terms: list[str] = field(default_factory=list)
    top_k: int | None = None
    sort_direction: str | None = None

    @property
    def schema_search_text(self) -> str:
        """生成用于表、列 schema 召回的扩展查询文本。"""
        parts = [
            self.original_text,
            *self.metrics,
            *self.dimensions,
            *self.entities,
            *self.filters,
            *self.time_expressions,
            *self.expanded_terms,
        ]
        return " ".join(dict.fromkeys(part for part in parts if part))


__all__ = ["ParsedQuery", "QueryExpansion"]
