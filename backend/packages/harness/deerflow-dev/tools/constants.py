"""DataAgent 工具层常量。"""

from __future__ import annotations

TABLE_RAG_TOOL_HINTS: tuple[str, ...] = (
    "tablerag_retrieve",
    "tablerag_raw_retrieve",
    "tablerag_search_evidences",
    "tablerag_search_tables",
    "tablerag_search_columns",
    "tablerag_search_values",
    "tablerag_expand_join_graph",
    "tablerag_validate_index",
)

TABLE_RAG_RETRIEVAL_TOOL_HINTS: tuple[str, ...] = TABLE_RAG_TOOL_HINTS[:-1]

TABLE_RAG_MUTATING_TOOL_SUFFIXES: frozenset[str] = frozenset(
    {
        "tablerag_initialize_indexes",
        "tablerag_sync_field_values",
    }
)

DATA_VALIDATE_SQL_TOOL_NAME = "data_validate_sql"
DATA_EXECUTE_SQL_TOOL_NAME = "data_execute_sql"
DATA_BUILD_CHART_SPEC_TOOL_NAME = "data_build_chart_spec"

DATA_AGENT_BUILTIN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        DATA_VALIDATE_SQL_TOOL_NAME,
        DATA_EXECUTE_SQL_TOOL_NAME,
        DATA_BUILD_CHART_SPEC_TOOL_NAME,
    }
)


def is_tablerag_tool_name(name: str) -> bool:
    """判断工具名是否来自 TableRAG MCP。

    Args:
        name: 工具名。

    Return:
        名称包含标准 TableRAG 工具后缀时返回 True。
    """
    normalized = name.strip().lower()
    return any(normalized in {hint, f"tablerag_{hint}"} for hint in (*TABLE_RAG_TOOL_HINTS, *TABLE_RAG_MUTATING_TOOL_SUFFIXES))


def is_readonly_tablerag_tool_name(name: str) -> bool:
    """判断工具名是否为允许暴露的只读 TableRAG 工具。

    Args:
        name: 工具名。

    Return:
        属于只读检索或索引校验工具时返回 True。
    """
    normalized = name.strip().lower()
    return any(normalized in {hint, f"tablerag_{hint}"} for hint in TABLE_RAG_TOOL_HINTS)


def is_tablerag_retrieval_tool_name(name: str) -> bool:
    """判断工具名是否为可推进检索阶段的 TableRAG 工具。

    `tablerag_validate_index` 仅用于健康检查，不能替代业务上下文检索。

    Args:
        name: 工具名。

    Return:
        属于 Evidence、表、列、字段值或 Join Graph 检索工具时返回 True。
    """
    normalized = name.strip().lower()
    return any(normalized in {hint, f"tablerag_{hint}"} for hint in TABLE_RAG_RETRIEVAL_TOOL_HINTS)
