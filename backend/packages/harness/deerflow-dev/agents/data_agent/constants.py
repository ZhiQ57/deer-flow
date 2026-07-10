"""DataAgent 实验性常量。"""

from __future__ import annotations

DATA_AGENT_NAME = "data-agent"

DATA_AGENT_SKILLS: frozenset[str] = frozenset(
    {
        "table-rag-agent",
        "data-analysis",
        "chart-visualization",
    }
)

# DataAgent 默认采用最小权限工具面：只保留 DeerFlow 内置框架工具、
# 只读 TableRAG MCP 和 DataAgent 专用 SQL/图表工具。
# 仅开放只读文件组供 DeerFlow 原生 Skill 渐进加载使用；后续还会经过
# DATA_AGENT_SAFE_LOCAL_TOOL_NAMES 二次过滤，不会暴露 ls/search/write/bash。
DATA_AGENT_TOOL_GROUPS: list[str] = ["file:read"]

DATA_AGENT_SAFE_LOCAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "ask_clarification",
        "present_files",
        "read_file",
        "task",
        "view_image",
    }
)

DEFAULT_ALIAS_MAP: dict[str, str] = {
    "GMV": "成交总额",
    "UV": "独立访客数",
    "PV": "页面浏览量",
    "DAU": "日活跃用户数",
    "MAU": "月活跃用户数",
    "客单": "客单价",
    "客单值": "客单价",
    "(例)": "病例数",
    "（例）": "病例数",
    "华东": "华东区域",
    "华南": "华南区域",
    "华北": "华北区域",
    "西南": "西南区域",
    "西北": "西北区域",
}

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

DATA_AGENT_RUNTIME_TOOL_NAMES: frozenset[str] = frozenset(
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
        属于只读检索/校验工具时返回 True。
    """
    normalized = name.strip().lower()
    return any(normalized in {hint, f"tablerag_{hint}"} for hint in TABLE_RAG_TOOL_HINTS)


def is_tablerag_retrieval_tool_name(name: str) -> bool:
    """判断工具名是否为可推进检索阶段的 TableRAG 工具。

    `tablerag_validate_index` 仅用于健康检查，不能替代业务上下文检索。

    Args:
        name: 工具名。

    Return:
        属于 Evidence/表/列/字段值/Join Graph 检索工具时返回 True。
    """
    normalized = name.strip().lower()
    return any(normalized in {hint, f"tablerag_{hint}"} for hint in TABLE_RAG_RETRIEVAL_TOOL_HINTS)
