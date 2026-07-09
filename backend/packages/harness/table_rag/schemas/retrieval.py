"""检索入参和出参标准结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..utils.validation import require_non_empty_string


@dataclass(frozen=True)
class RetrievalOptions:
    """检索参数统一配置。"""

    evidence_top_k: int = 5
    evidence_min_score: float | None = None
    table_top_k: int = 10
    table_min_score: float | None = None
    column_top_k: int = 20
    column_min_score: float | None = None
    value_top_k: int = 5
    value_min_score: float | None = None
    join_max_hops: int = 2
    final_table_top_k: int = 10
    final_column_top_k: int = 20
    table_names: list[str] | None = None
    column_names: list[str] | None = None

    def __post_init__(self) -> None:
        """校验检索参数，避免非法 top_k、分数和过滤列表进入运行时。

        Args:
            无。

        Returns:
            None。
        """
        _require_positive_int(self.evidence_top_k, "evidence_top_k")
        _require_positive_int(self.table_top_k, "table_top_k")
        _require_positive_int(self.column_top_k, "column_top_k")
        _require_positive_int(self.value_top_k, "value_top_k")
        _require_non_negative_int(self.join_max_hops, "join_max_hops")
        _require_positive_int(self.final_table_top_k, "final_table_top_k")
        _require_positive_int(self.final_column_top_k, "final_column_top_k")
        _require_min_score(self.evidence_min_score, "evidence_min_score")
        _require_min_score(self.table_min_score, "table_min_score")
        _require_min_score(self.column_min_score, "column_min_score")
        _require_min_score(self.value_min_score, "value_min_score")
        _require_non_empty_string_list(self.table_names, "table_names")
        _require_non_empty_string_list(self.column_names, "column_names")


@dataclass(frozen=True)
class HybridRetrievalResult:
    """混合检索最终结果统一返回。"""

    query: str
    tables: list[TableRetrievalResult]
    columns: list[ColumnRetrievalResult]
    values: list[ValueRetrievalResult]
    join_graphs: list[JoinGraphRetrievalResult]
    evidences: list[EvidenceRetrievalResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验混合检索结果核心字段。

        Args:
            无。

        Returns:
            None。
        """
        require_non_empty_string(self.query, "result.query")
        _require_mapping(self.metadata, "result.metadata")


@dataclass(frozen=True)
class EvidenceRetrievalResult:
    """业务规则和证据召回统一返回结果。"""

    evidence_content: str
    score: float
    evidence_type: str | None = None
    triggers: list[str] = field(default_factory=list)
    retrieval_text: str | None = None
    description: str | None = None
    status: int = 1
    source_scores: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验证据召回结果核心字段。"""
        require_non_empty_string(self.evidence_content, "evidence_result.evidence_content")
        _require_finite_score(self.score, "evidence_result.score")
        _require_string_list(self.triggers, "evidence_result.triggers")
        _require_mapping(self.source_scores, "evidence_result.source_scores")
        _require_mapping(self.metadata, "evidence_result.metadata")


@dataclass(frozen=True)
class TableRetrievalResult:
    """表结构召回统一返回结果。"""

    table_name: str
    score: float
    table_label: str | None = None
    table_entities: list[str] = field(default_factory=list)
    table_describe: str | None = None
    source_scores: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验表召回结果核心字段。

        Args:
            无。

        Returns:
            None。
        """
        require_non_empty_string(self.table_name, "table_result.table_name")
        _require_finite_score(self.score, "table_result.score")
        _require_string_list(self.table_entities, "table_result.table_entities")
        _require_mapping(self.source_scores, "table_result.source_scores")
        _require_mapping(self.metadata, "table_result.metadata")


@dataclass(frozen=True)
class ColumnRetrievalResult:
    """列字段召回统一返回结果。"""

    table_name: str
    column_name: str
    score: float
    column_comment: str | None = None
    column_entities: list[str] = field(default_factory=list)
    source_scores: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验列召回结果核心字段。

        Args:
            无。

        Returns:
            None。
        """
        require_non_empty_string(self.table_name, "column_result.table_name")
        require_non_empty_string(self.column_name, "column_result.column_name")
        _require_finite_score(self.score, "column_result.score")
        _require_string_list(self.column_entities, "column_result.column_entities")
        _require_mapping(self.source_scores, "column_result.source_scores")
        _require_mapping(self.metadata, "column_result.metadata")


@dataclass(frozen=True)
class ColumnTableMapping:
    """列字段到表的反向映射，用于由字段召回补全候选表。"""

    column_name: str
    table_name: str
    column_comment: str | None = None

    def __post_init__(self) -> None:
        """校验字段到表映射。

        Args:
            无。

        Returns:
            None。
        """
        require_non_empty_string(self.column_name, "column_mapping.column_name")
        require_non_empty_string(self.table_name, "column_mapping.table_name")


@dataclass(frozen=True)
class ValueRetrievalResult:
    """字段值倒排索引检索统一返回结果。"""

    raw_value: str
    table_name: str
    column_name: str
    score: float
    aliases: list[str] = field(default_factory=list)
    normalized_value: str | None = None
    table_comment: str | None = None
    column_comment: str | None = None
    source_schema: str | None = None
    source_table: str | None = None
    source_column: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        """校验字段值召回结果核心字段。

        Args:
            无。

        Returns:
            None。
        """
        require_non_empty_string(self.raw_value, "value_result.raw_value")
        require_non_empty_string(self.table_name, "value_result.table_name")
        require_non_empty_string(self.column_name, "value_result.column_name")
        _require_finite_score(self.score, "value_result.score")
        _require_string_list(self.aliases, "value_result.aliases")
        _require_mapping(self.metadata, "value_result.metadata")


@dataclass(frozen=True)
class JoinGraphRetrievalResult:
    """表关联图谱检索统一返回结果。"""

    node: str | None = None
    edges: list[JoinEdge] = field(default_factory=list)
    paths: list[JoinPath] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验 Join Graph 召回结果。

        Args:
            无。

        Returns:
            None。
        """
        _require_mapping(self.metadata, "join_graph_result.metadata")


@dataclass(frozen=True)
class JoinEdge:
    """Schema Join Graph 的边，记录两张表之间的 JOIN 条件。"""

    source_table: str
    target_table: str
    join_condition: str
    edge_type: str = "join"
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验 Join Graph 边。

        Args:
            无。

        Returns:
            None。
        """
        require_non_empty_string(self.source_table, "join_edge.source_table")
        require_non_empty_string(self.target_table, "join_edge.target_table")
        require_non_empty_string(self.join_condition, "join_edge.join_condition")
        require_non_empty_string(self.edge_type, "join_edge.edge_type")
        _require_finite_score(self.weight, "join_edge.weight")
        _require_mapping(self.metadata, "join_edge.metadata")


@dataclass(frozen=True)
class JoinPath:
    """Schema Join Graph 的路径，表示候选表之间可连接的 JOIN 链路。"""

    tables: list[str]
    edges: list[JoinEdge]
    score: float

    def __post_init__(self) -> None:
        """校验 Join Graph 路径。

        Args:
            无。

        Returns:
            None。
        """
        if len(self.tables) < 2:
            raise ValueError("join_path.tables must contain at least two tables")
        _require_non_empty_string_list(self.tables, "join_path.tables")
        _require_finite_score(self.score, "join_path.score")


def _require_positive_int(value: int, label: str) -> None:
    """校验正整数参数。

    Args:
        value: 待校验数值。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _require_non_negative_int(value: int, label: str) -> None:
    """校验非负整数参数。

    Args:
        value: 待校验数值。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _require_min_score(value: float | None, label: str) -> None:
    """校验最低分阈值。

    Args:
        value: 待校验分数。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if value is None:
        return
    _require_finite_score(value, label)
    if value < 0:
        raise ValueError(f"{label} must be >= 0")


def _require_finite_score(value: float, label: str) -> None:
    """校验有限数值分数。

    Args:
        value: 待校验分数。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric")
    if value != value or value in {float("inf"), float("-inf")}:
        raise ValueError(f"{label} must be finite")


def _require_non_empty_string_list(value: list[str] | None, label: str) -> None:
    """校验可选非空字符串列表。

    Args:
        value: 待校验列表。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if value is None:
        return
    _require_string_list(value, label)
    for index, item in enumerate(value):
        require_non_empty_string(item, f"{label}[{index}]")


def _require_string_list(value: list[str], label: str) -> None:
    """校验字符串列表类型。

    Args:
        value: 待校验列表。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"{label}[{index}] must be a string")


def _require_mapping(value: dict[str, Any], label: str) -> None:
    """校验字典字段。

    Args:
        value: 待校验对象。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")


__all__ = [
    "ColumnRetrievalResult",
    "ColumnTableMapping",
    "EvidenceRetrievalResult",
    "HybridRetrievalResult",
    "JoinEdge",
    "JoinGraphRetrievalResult",
    "JoinPath",
    "RetrievalOptions",
    "TableRetrievalResult",
    "ValueRetrievalResult",
]
