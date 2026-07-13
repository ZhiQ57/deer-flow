"""DataAgent ChartSpec 工具。"""

from __future__ import annotations

import json
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langgraph.types import Command

from tools.builtins._data_agent_tool import DataAgentRuntime, build_tool_command
from tools.chart_spec import build_chart_spec
from tools.constants import DATA_BUILD_CHART_SPEC_TOOL_NAME


def normalize_chart_y(value: list[str] | str | None) -> list[str] | None:
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
            y=normalize_chart_y(y),
            series=series,
            title=title,
        )
    except ValueError as exc:
        return build_tool_command(
            tool_name=DATA_BUILD_CHART_SPEC_TOOL_NAME,
            tool_call_id=tool_call_id,
            content={"ok": False, "error": str(exc)},
            update={
                "data_agent_stage": "chart_failed",
                "data_chart_spec": None,
            },
            error=True,
        )

    return build_tool_command(
        tool_name=DATA_BUILD_CHART_SPEC_TOOL_NAME,
        tool_call_id=tool_call_id,
        content={"ok": True, "chart_spec": spec},
        update={
            "data_agent_stage": "chart_ready",
            "data_chart_spec": spec,
        },
    )
