"""DataAgent SQLRAG MCP 单工具合同。"""

from __future__ import annotations

SQLRAG_RETRIEVE_TOOL_NAME = "sqlrag_retrieve"
"""DataAgent 唯一允许暴露的 SQLRAG MCP 工具名。"""

SQLRAG_OPERATIONS = frozenset(
    {
        "hybrid-search",
        "search-evidences",
        "search-tables",
        "search-columns",
        "search-values",
        "expand-join-graph",
    }
)
"""sqlrag_retrieve 支持的六种只读检索操作。"""

SQLRAG_SINGLE_ROUTE_COLLECTIONS = {
    "search-evidences": "evidences",
    "search-tables": "tables",
    "search-columns": "columns",
    "search-values": "values",
    "expand-join-graph": "join_graphs",
}
"""单路操作与 DataAgent 检索集合的映射。"""

_LEGACY_SQLRAG_TOOL_NAMES = (
    "tablerag_retrieve",
    "tablerag_raw_retrieve",
    "tablerag_search_evidences",
    "tablerag_search_tables",
    "tablerag_search_columns",
    "tablerag_search_values",
    "tablerag_expand_join_graph",
    "tablerag_validate_index",
    "tablerag_initialize_indexes",
    "tablerag_sync_field_values",
)


def is_sqlrag_retrieval_tool_name(name: object) -> bool:
    """判断工具名是否严格等于 SQLRAG 唯一工具名。

    Args:
        name: 待检查的工具名。

    Returns:
        仅当名称严格等于 ``sqlrag_retrieve`` 时返回 True。
    """
    return isinstance(name, str) and name == SQLRAG_RETRIEVE_TOOL_NAME


def is_legacy_sqlrag_tool_name(name: object) -> bool:
    """判断 MCP 工具是否属于应从 DataAgent 移除的旧合同。

    Args:
        name: 待检查的工具名。

    Returns:
        旧 TableRAG 多工具名或带 Server 前缀的 SQLRAG 工具返回 True。
    """
    if not isinstance(name, str) or name == SQLRAG_RETRIEVE_TOOL_NAME:
        return False
    return name.endswith(f"_{SQLRAG_RETRIEVE_TOOL_NAME}") or any(name == legacy or name.endswith(f"_{legacy}") for legacy in _LEGACY_SQLRAG_TOOL_NAMES)


def require_sqlrag_operation(value: object) -> str:
    """校验并返回 SQLRAG 检索操作。

    Args:
        value: MCP 返回或工具参数中的 operation。

    Returns:
        已校验的 operation 字符串。

    Raises:
        ValueError: operation 不属于六种只读检索操作。
    """
    if not isinstance(value, str) or value not in SQLRAG_OPERATIONS:
        supported = ", ".join(sorted(SQLRAG_OPERATIONS))
        raise ValueError(f"SQLRAG operation 必须是以下值之一：{supported}。")
    return value
