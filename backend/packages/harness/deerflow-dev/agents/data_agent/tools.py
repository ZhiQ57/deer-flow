"""DataAgent 专用 SQL 和 ChartSpec 工具。"""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from .chart import build_chart_spec
from .constants import (
    DATA_AGENT_RUNTIME_TOOL_NAMES,
    DATA_BUILD_CHART_SPEC_TOOL_NAME,
    DATA_EXECUTE_SQL_TOOL_NAME,
    DATA_VALIDATE_SQL_TOOL_NAME,
)
from .database import MySQLExecutionSettings, execute_readonly_sql, format_safe_database_error
from .sql_validation import SQLValidationError, sql_sha256, validate_readonly_sql
from .state import DataAgentState

DataAgentRuntime = ToolRuntime[dict[str, Any], DataAgentState]


def _normalize_chart_y(value: list[str] | str | None) -> list[str] | None:
    """兼容模型把 Y 轴列表作为 JSON 字符串传入。

    Args:
        value: 字段列表、JSON 字符串、逗号分隔字符串或 None。

    Return:
        规范化字段列表；未配置时返回 None。
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = value.strip()
    if not text:
        return None
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Y 轴 JSON 字符串格式不正确。") from exc
        if not isinstance(parsed, list):
            raise ValueError("Y 轴 JSON 必须是字符串数组。")
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in text.split(",") if item.strip()]


def _command(
    *,
    tool_name: str,
    tool_call_id: str,
    content: dict[str, Any],
    update: dict[str, Any],
    error: bool = False,
) -> Command:
    """构造带状态更新的 DataAgent 工具结果。

    Args:
        tool_name: 工具名。
        tool_call_id: 工具调用 ID。
        content: 工具结果内容。
        update: 图状态更新。
        error: 是否为错误结果。

    Return:
        LangGraph Command。
    """
    message = ToolMessage(
        content=json.dumps(content, ensure_ascii=False, default=str),
        tool_call_id=tool_call_id,
        name=tool_name,
        status="error" if error else "success",
        additional_kwargs={"data_agent_stage": update.get("data_agent_stage")},
    )
    return Command(update={**update, "messages": [message]})


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
        return _command(
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
    return _command(
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
        return _command(
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
        return _command(
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

    return _command(
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


@tool(DATA_BUILD_CHART_SPEC_TOOL_NAME, parse_docstring=True)
def data_build_chart_spec_tool(
    runtime: DataAgentRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId],
    chart_type: str = "auto",
    x: str | None = None,
    y: list[str] | str | None = None,
    series: str | None = None,
    title: str | None = None,
) -> Command:
    """根据最近一次成功 SQL 结果构造图表规格。

    Args:
        chart_type: auto/bar/line/pie/scatter/table/kpi。
        x: 可选 X 轴字段。
        y: 可选 Y 轴字段列表。
        series: 可选系列字段。
        title: 可选图表标题。

    Returns:
        可供前端或最终答案消费的 ChartSpec。
    """
    execution = (runtime.state or {}).get("data_sql_execution")
    try:
        spec = build_chart_spec(
            execution or {},
            chart_type=chart_type,
            x=x,
            y=_normalize_chart_y(y),
            series=series,
            title=title,
        )
    except ValueError as exc:
        return _command(
            tool_name=DATA_BUILD_CHART_SPEC_TOOL_NAME,
            tool_call_id=tool_call_id,
            content={"ok": False, "error": str(exc)},
            update={
                "data_agent_stage": "chart_failed",
                "data_chart_spec": None,
            },
            error=True,
        )

    return _command(
        tool_name=DATA_BUILD_CHART_SPEC_TOOL_NAME,
        tool_call_id=tool_call_id,
        content={"ok": True, "chart_spec": spec},
        update={
            "data_agent_stage": "chart_ready",
            "data_chart_spec": spec,
        },
    )


def build_data_agent_runtime_tools() -> list[Any]:
    """构造 DataAgent 专用工具列表。

    Args:
        无。

    Return:
        SQL 校验、SQL 执行和 ChartSpec 工具。
    """
    tools = [
        data_validate_sql_tool,
        data_execute_sql_tool,
        data_build_chart_spec_tool,
    ]
    if {item.name for item in tools} != set(DATA_AGENT_RUNTIME_TOOL_NAMES):
        raise RuntimeError("DataAgent runtime tool registry is inconsistent.")
    return tools
