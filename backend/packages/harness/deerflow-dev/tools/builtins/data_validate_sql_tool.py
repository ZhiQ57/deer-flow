"""DataAgent SQL 校验工具。"""

from __future__ import annotations

from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langgraph.types import Command

from tools.builtins._data_agent_tool import DataAgentRuntime, build_tool_command
from tools.constants import DATA_VALIDATE_SQL_TOOL_NAME
from tools.database import MySQLExecutionSettings
from tools.sql_validation import SQLValidationError, validate_readonly_sql


@tool(DATA_VALIDATE_SQL_TOOL_NAME, parse_docstring=True)
def data_validate_sql_tool(
    runtime: DataAgentRuntime,
    sql: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """按 MySQL 方言验证单条只读 SQL，并生成受行数上限保护的可执行 SQL。

    Args:
        sql: 基于 TableRAG 已确认表、字段和 Join 路径生成的 SQL。

    Returns:
        SQL 校验结果和规范化后的 executable_sql。
    """
    try:
        settings = MySQLExecutionSettings.from_env()
        result = validate_readonly_sql(
            sql,
            max_rows=settings.max_rows,
            allowed_database=settings.database,
        )
    except (SQLValidationError, ValueError) as exc:
        validation = {
            "valid": False,
            "original_sql": sql,
            "error": str(exc),
        }
        return build_tool_command(
            tool_name=DATA_VALIDATE_SQL_TOOL_NAME,
            tool_call_id=tool_call_id,
            content=validation,
            update={
                "data_agent_stage": "sql_validation_failed",
                "data_generated_sql": sql,
                "data_sql_validation": validation,
                "data_chart_spec": None,
            },
            error=True,
        )

    validation = result.to_dict()
    return build_tool_command(
        tool_name=DATA_VALIDATE_SQL_TOOL_NAME,
        tool_call_id=tool_call_id,
        content=validation,
        update={
            "data_agent_stage": "sql_validated",
            "data_generated_sql": result.executable_sql,
            "data_sql_validation": validation,
            "data_chart_spec": None,
        },
    )
