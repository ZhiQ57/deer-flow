"""DataAgent 用户轮次隔离 middleware。"""

# ADD: DataAgent 正式查询闭环新增，新可见用户消息必须使旧标签授权和 SQL 快照失效。
from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from langchain_core.messages import HumanMessage

from deerflow.agents.human_input import read_human_input_response


# ADD: 统一识别当前可见用户轮次，human-input 隐藏回复不创建新业务轮次。
def current_visible_turn_id(state: Mapping[str, Any] | None) -> str | None:
    """读取最新可见 HumanMessage 的稳定轮次 ID。"""
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
