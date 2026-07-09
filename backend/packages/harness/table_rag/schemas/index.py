"""索引记录和字段值同步目标结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..utils.validation import require_non_empty_string, validate_safe_identifier


_SUPPORTED_FIELD_FILTER_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "startswith",
    "endswith",
    "is_null",
    "is_not_null",
}


@dataclass(frozen=True)
class FieldValueFilter:
    """字段值同步安全过滤条件，用结构化 DSL 替代裸 SQL where 子句。"""

    column_name: str
    operator: str = "eq"
    value: Any | None = None
    values: list[Any] | None = None

    def __post_init__(self) -> None:
        """校验过滤字段名、操作符和值配置。

        Args:
            无。

        Returns:
            None。
        """
        validate_safe_identifier(self.column_name, "filter.column_name")
        if not isinstance(self.operator, str) or not self.operator.strip():
            raise ValueError("filter.operator must be a non-empty string")
        normalized_operator = self.operator.strip().lower()
        if normalized_operator not in _SUPPORTED_FIELD_FILTER_OPERATORS:
            raise ValueError(
                "filter.operator must be one of: "
                + ", ".join(sorted(_SUPPORTED_FIELD_FILTER_OPERATORS))
            )
        object.__setattr__(self, "operator", normalized_operator)
        if normalized_operator in {"is_null", "is_not_null"}:
            if self.value is not None or self.values is not None:
                raise ValueError(f"filter.operator={normalized_operator!r} must not configure value or values")
            return
        if normalized_operator in {"in", "not_in"}:
            if not isinstance(self.values, list) or not self.values:
                raise ValueError(f"filter.operator={normalized_operator!r} requires non-empty values")
            return
        if self.value is None:
            raise ValueError(f"filter.operator={normalized_operator!r} requires value")
        if self.values is not None:
            raise ValueError(f"filter.operator={normalized_operator!r} must not configure values")


def _coerce_filters(filters: list[FieldValueFilter | dict[str, Any]]) -> list[FieldValueFilter]:
    """校验并归一化字段值过滤条件列表。

    Args:
        filters: 字段值同步安全过滤条件列表。

    Returns:
        归一化后的过滤条件列表。
    """
    if not isinstance(filters, list):
        raise ValueError("filters must be a list")
    normalized_filters: list[FieldValueFilter] = []
    for item in filters:
        if isinstance(item, FieldValueFilter):
            normalized_filters.append(item)
        elif isinstance(item, dict):
            normalized_filters.append(FieldValueFilter(**item))
        else:
            raise TypeError("filters must contain FieldValueFilter instances or mappings")
    return normalized_filters


@dataclass(frozen=True)
class FieldValueIndexTarget:
    """字段值索引目标字段，描述一个需要抽取 distinct 字段值的列。"""

    column_name: str
    source_column: str | None = None
    column_comment: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        """校验字段同步目标配置。

        Args:
            无。

        Returns:
            None。
        """
        require_non_empty_string(self.column_name, "field.column_name")
        validate_safe_identifier(self.resolved_source_column, "field.source_column")

    @property
    def resolved_source_column(self) -> str:
        """获取真实源字段名。"""
        return self.source_column or self.column_name


@dataclass(frozen=True)
class TableValueIndexTarget:
    """字段值索引目标表，描述逻辑模型和真实来源表。"""

    table_name: str
    table_comment: str | None
    source_table: str
    source_schema: str = "public"
    enabled: bool = True
    filters: list[FieldValueFilter] = field(default_factory=list)
    unsafe_where_clause: str | None = None
    limit: int | None = 10000
    fields: list[FieldValueIndexTarget] = field(default_factory=list)

    def __post_init__(self) -> None:
        """校验字段值同步目标配置。

        Args:
            无。

        Returns:
            None。
        """
        require_non_empty_string(self.table_name, "target.table_name")
        validate_safe_identifier(self.source_schema, "target.source_schema")
        validate_safe_identifier(self.source_table, "target.source_table")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("target.limit must be a positive integer or None")
        object.__setattr__(self, "filters", _coerce_filters(self.filters))
        for field_item in self.fields:
            if not isinstance(field_item, FieldValueIndexTarget):
                raise TypeError("target.fields must contain FieldValueIndexTarget instances")


@dataclass(frozen=True)
class FieldValueRecord:
    """待写入索引表的字段值记录。"""

    table_name: str
    table_comment: str | None
    column_name: str
    column_comment: str | None
    raw_value: str
    aliases: list[str] = field(default_factory=list)
    normalized_value: str | None = None
    source_schema: str | None = None
    source_table: str | None = None
    source_column: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验待写入字段值记录的核心字段。

        Args:
            无。

        Returns:
            None。
        """
        require_non_empty_string(self.table_name, "record.table_name")
        require_non_empty_string(self.column_name, "record.column_name")
        require_non_empty_string(self.raw_value, "record.raw_value")
        if not isinstance(self.aliases, list):
            raise ValueError("record.aliases must be a list")
        if not isinstance(self.metadata, dict):
            raise ValueError("record.metadata must be a mapping")


@dataclass(frozen=True)
class FieldValueSyncReport:
    """单个字段值同步报告。"""

    table_name: str
    column_name: str
    source_column: str
    status: str
    indexed_count: int = 0
    duration_ms: float = 0.0
    skip_reason: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        """校验字段级同步报告。"""
        require_non_empty_string(self.table_name, "sync_report.table_name")
        require_non_empty_string(self.column_name, "sync_report.column_name")
        require_non_empty_string(self.source_column, "sync_report.source_column")
        require_non_empty_string(self.status, "sync_report.status")
        if self.indexed_count < 0:
            raise ValueError("sync_report.indexed_count must be >= 0")
        if self.duration_ms < 0:
            raise ValueError("sync_report.duration_ms must be >= 0")


@dataclass(frozen=True)
class TableValueSyncReport:
    """单张逻辑表字段值同步报告。"""

    table_name: str
    status: str
    indexed_count: int = 0
    duration_ms: float = 0.0
    fields: list[FieldValueSyncReport] = field(default_factory=list)
    skip_reason: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        """校验表级同步报告。"""
        require_non_empty_string(self.table_name, "sync_report.table_name")
        require_non_empty_string(self.status, "sync_report.status")
        if self.indexed_count < 0:
            raise ValueError("sync_report.indexed_count must be >= 0")
        if self.duration_ms < 0:
            raise ValueError("sync_report.duration_ms must be >= 0")


@dataclass(frozen=True)
class ValueIndexSyncReport:
    """一次字段值索引同步总报告。"""

    status: str
    total_indexed_count: int = 0
    duration_ms: float = 0.0
    tables: list[TableValueSyncReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """校验字段值同步总报告。"""
        require_non_empty_string(self.status, "sync_report.status")
        if self.total_indexed_count < 0:
            raise ValueError("sync_report.total_indexed_count must be >= 0")
        if self.duration_ms < 0:
            raise ValueError("sync_report.duration_ms must be >= 0")

    @property
    def table_counts(self) -> dict[str, int]:
        """返回非跳过表的写入数量摘要。"""
        return {
            table.table_name: table.indexed_count
            for table in self.tables
            if table.status != "skipped"
        }
