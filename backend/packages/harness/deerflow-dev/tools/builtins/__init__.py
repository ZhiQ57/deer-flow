"""DataAgent 内置工具。"""

from typing import Any

from deerflow.tools import entity_extract_tool, publish_query_labels_tool
from tools.builtins.chart_spec_tool import data_build_chart_spec_tool
from tools.builtins.data_execute_sql_tool import data_execute_sql_tool
from tools.builtins.data_validate_sql_tool import data_validate_sql_tool
from tools.constants import DATA_AGENT_BUILTIN_TOOL_NAMES

__all__ = [
    "data_build_chart_spec_tool",
    "data_execute_sql_tool",
    "data_validate_sql_tool",
    "entity_extract_tool",
    "get_data_agent_tools",
    "publish_query_labels_tool",
]


def get_data_agent_tools() -> list[Any]:
    """返回 DataAgent 内置工具列表。

    Args:
        无。

    Return:
        标签声明、可选 QueryContext、SQL 校验、SQL 执行和 ChartSpec 工具。
    """
    tools = [
        entity_extract_tool,
        publish_query_labels_tool,
        data_validate_sql_tool,
        data_execute_sql_tool,
        data_build_chart_spec_tool,
    ]
    if {item.name for item in tools} != set(DATA_AGENT_BUILTIN_TOOL_NAMES):
        raise RuntimeError("DataAgent built-in tool registry is inconsistent.")
    return tools
