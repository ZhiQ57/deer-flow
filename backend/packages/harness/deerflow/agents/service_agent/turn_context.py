"""DataAgent 可见用户消息上下文工具函数。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from langchain_core.messages import HumanMessage

from deerflow.agents.human_input import read_human_input_response


def current_visible_turn_id(state: Mapping[str, Any] | None) -> str | None:
    """读取最新可见 HumanMessage 的稳定 ID。

    Args:
        state: LangGraph 当前线程状态。

    Returns:
        最新真实用户消息 ID；如果只有 hidden human-input 回复或没有用户消息则返回 None。
    """
    messages = state.get("messages") if isinstance(state, Mapping) else None
    if not isinstance(messages, Sequence):
        return None
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        if read_human_input_response(message.additional_kwargs) is not None:
            continue
        if isinstance(message.id, str) and message.id:
            return message.id
        digest = sha256(str(message.content).encode("utf-8")).hexdigest()[:24]
        return f"human:{digest}"
    return None
