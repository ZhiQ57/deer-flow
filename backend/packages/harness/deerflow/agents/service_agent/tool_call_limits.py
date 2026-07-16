"""DataAgent 同阶段工具调用串行化辅助方法。"""

# ADD: DataAgent 正式查询闭环新增，防止同一 AIMessage 并行创建冲突的检索、标签或 SQL 快照。
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls


def keep_first_matching_tool_call(
    state: Mapping[str, Any],
    predicate: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any] | None:
    """只保留当前 AIMessage 中第一个匹配的工具调用。

    Args:
        state: 当前 Agent 状态。
        predicate: 判断工具调用是否属于需要串行化的业务阶段。

    Returns:
        包含替换 AIMessage 的状态更新；没有重复匹配时返回 None。
    """
    messages = state.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)) or not messages:
        return None
    last_message = messages[-1]
    tool_calls = getattr(last_message, "tool_calls", None)
    if not isinstance(tool_calls, list):
        return None
    kept: list[dict[str, Any]] = []
    matched = False
    dropped = False
    for item in tool_calls:
        if not isinstance(item, Mapping):
            kept.append(item)
            continue
        tool_call = dict(item)
        if predicate(tool_call):
            if matched:
                dropped = True
                continue
            matched = True
        kept.append(tool_call)
    if not dropped:
        return None
    return {"messages": [clone_ai_message_with_tool_calls(last_message, kept)]}
