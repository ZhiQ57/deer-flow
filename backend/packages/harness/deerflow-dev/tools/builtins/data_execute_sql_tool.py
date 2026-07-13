"""DataAgent SQL 执行工具。"""

from __future__ import annotations

from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langgraph.types import Command

from tools.builtins._data_agent_tool import DataAgentRuntime, build_tool_command
from tools.constants import DATA_EXECUTE_SQL_TOOL_NAME
from tools.database import MySQLExecutionSettings, execute_readonly_sql, format_safe_database_error
from tools.sql_validation import sql_sha256


@tool(DATA_EXECUTE_SQL_TOOL_NAME, parse_docstring=True)
def data_execute_sql_tool(
    runtime: DataAgentRuntime,
    sql: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """在只读事务中执行已经通过 data_validate_sql 校验的 MySQL SQL。

    Args:
        sql: `data_validate_sql` 返回的 executable_sql，必须保持一致。

    Returns:
        受行数、单元格和总字符预算保护的查询结果。
    """
    state = runtime.state or {}
    validation = state.get("data_sql_validation")
    expected_digest = validation.get("sql_sha256") if isinstance(validation, dict) and validation.get("valid") is True else None
    if not expected_digest or sql_sha256(sql) != expected_digest:
        execution = {
            "ok": False,
            "error": "待执行 SQL 必须与最近一次 data_validate_sql 返回的 executable_sql 完全一致。",
        }
        return build_tool_command(
            tool_name=DATA_EXECUTE_SQL_TOOL_NAME,
            tool_call_id=tool_call_id,
            content=execution,
            update={
                "data_agent_stage": "sql_execution_failed",
                "data_sql_execution": execution,
                "data_chart_spec": None,
            },
            error=True,
        )

    settings: MySQLExecutionSettings | None = None
    try:
        settings = MySQLExecutionSettings.from_env()
        result = execute_readonly_sql(sql, settings=settings)
    except Exception as exc:
        execution = {
            "ok": False,
            "error": format_safe_database_error(exc, settings=settings),
        }
        return build_tool_command(
            tool_name=DATA_EXECUTE_SQL_TOOL_NAME,
            tool_call_id=tool_call_id,
            content=execution,
            update={
                "data_agent_stage": "sql_execution_failed",
                "data_sql_execution": execution,
                "data_chart_spec": None,
            },
            error=True,
        )

    return build_tool_command(
        tool_name=DATA_EXECUTE_SQL_TOOL_NAME,
        tool_call_id=tool_call_id,
        content=result,
        update={
            "data_agent_stage": "sql_executed",
            "data_generated_sql": result["executable_sql"],
            "data_sql_execution": result,
            "data_last_successful_sql_execution": result,
            "data_chart_spec": None,
        },
    )
