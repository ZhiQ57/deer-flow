"""DataAgent 工具层常量。"""

from __future__ import annotations

from deerflow.agents.service_agent.sqlrag_contract import SQLRAG_OPERATIONS, SQLRAG_RETRIEVE_TOOL_NAME, is_sqlrag_retrieval_tool_name

ENTITY_EXTRACT_TOOL_NAME = "entity_extract_tool"
PUBLISH_QUERY_LABELS_TOOL_NAME = "publish_query_labels"

SQLRAG_TOOL_NAME = SQLRAG_RETRIEVE_TOOL_NAME
SQLRAG_OPERATION_NAMES: tuple[str, ...] = tuple(sorted(SQLRAG_OPERATIONS))

DATA_VALIDATE_SQL_TOOL_NAME = "data_validate_sql"
DATA_EXECUTE_SQL_TOOL_NAME = "data_execute_sql"
DATA_BUILD_CHART_SPEC_TOOL_NAME = "data_build_chart_spec"

DATA_AGENT_BUILTIN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        ENTITY_EXTRACT_TOOL_NAME,
        PUBLISH_QUERY_LABELS_TOOL_NAME,
        DATA_VALIDATE_SQL_TOOL_NAME,
        DATA_EXECUTE_SQL_TOOL_NAME,
        DATA_BUILD_CHART_SPEC_TOOL_NAME,
    }
)


def is_sqlrag_tool_name(name: str) -> bool:
    """判断工具名是否为 DataAgent 唯一 SQLRAG MCP 工具。

    Args:
        name: 工具名。

    Returns:
        名称严格等于 ``sqlrag_retrieve`` 时返回 True。
    """
    return is_sqlrag_retrieval_tool_name(name)
