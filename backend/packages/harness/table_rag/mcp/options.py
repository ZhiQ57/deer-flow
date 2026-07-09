"""MCP 工具入参到 TableRAG 检索参数的转换。"""

from __future__ import annotations

from typing import Any

from ..schemas import RetrievalOptions


def build_retrieval_options(
    *,
    evidence_top_k: int = 5,
    table_top_k: int = 10,
    column_top_k: int = 20,
    value_top_k: int = 5,
    join_max_hops: int = 2,
    final_table_top_k: int = 10,
    final_column_top_k: int = 20,
    table_names: Any = None,
    column_names: Any = None,
    max_top_k: int = 100,
    max_join_hops: int = 5,
) -> RetrievalOptions:
    """把 MCP 工具参数转换为 SDK 标准检索参数。

    Args:
        evidence_top_k: Evidence 召回数量。
        table_top_k: 表召回数量。
        column_top_k: 列召回数量。
        value_top_k: 字段值召回数量。
        join_max_hops: Join Graph 最大跳数。
        final_table_top_k: 最终表候选数量。
        final_column_top_k: 最终列候选数量。
        table_names: 可选表名过滤，支持列表或逗号分隔字符串。
        column_names: 可选列名过滤，支持列表或逗号分隔字符串。
        max_top_k: 单次工具允许的最大 top_k。
        max_join_hops: 单次工具允许的最大 Join Graph 跳数。

    Returns:
        SDK 检索参数对象。
    """
    _validate_top_k(evidence_top_k, "evidence_top_k", max_top_k)
    _validate_top_k(table_top_k, "table_top_k", max_top_k)
    _validate_top_k(column_top_k, "column_top_k", max_top_k)
    _validate_top_k(value_top_k, "value_top_k", max_top_k)
    _validate_top_k(final_table_top_k, "final_table_top_k", max_top_k)
    _validate_top_k(final_column_top_k, "final_column_top_k", max_top_k)
    _validate_join_hops(join_max_hops, max_join_hops)
    return RetrievalOptions(
        evidence_top_k=evidence_top_k,
        table_top_k=table_top_k,
        column_top_k=column_top_k,
        value_top_k=value_top_k,
        join_max_hops=join_max_hops,
        final_table_top_k=final_table_top_k,
        final_column_top_k=final_column_top_k,
        table_names=_coerce_optional_string_list(table_names, "table_names"),
        column_names=_coerce_optional_string_list(column_names, "column_names"),
    )


def _validate_top_k(value: int, label: str, max_top_k: int) -> None:
    """校验 MCP 暴露的 top_k 参数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    if value > max_top_k:
        raise ValueError(f"{label} must be <= {max_top_k}")


def _validate_join_hops(value: int, max_join_hops: int) -> None:
    """校验 Join Graph 最大跳数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("join_max_hops must be a non-negative integer")
    if value > max_join_hops:
        raise ValueError(f"join_max_hops must be <= {max_join_hops}")


def _coerce_optional_string_list(value: Any, label: str) -> list[str] | None:
    """把 MCP 输入中的列表或逗号分隔字符串归一化为字符串列表。"""
    if value is None:
        return None
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        raise TypeError(f"{label} must be a string list or comma-separated string")
    cleaned = [item for item in items if item]
    return cleaned or None
