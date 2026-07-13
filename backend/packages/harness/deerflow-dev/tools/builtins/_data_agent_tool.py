"""DataAgent built-in tool 共用类型与结果构造。"""

from __future__ import annotations

import json
from typing import Any

from agents.thread_state import DataAgentState
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.types import Command

DataAgentRuntime = ToolRuntime[dict[str, Any], DataAgentState]


def build_tool_command(
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
