"""DataAgent 用户轮次隔离 middleware。"""

# ADD: DataAgent 正式查询闭环新增，新可见用户消息必须使旧标签授权和 SQL 快照失效。
from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from deerflow.agents.human_input import read_human_input_response

from .config import DataQueryServiceAbilityConfig
from .state import get_active_service_state, make_service_state


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


class DataAgentTurnResetMiddleware(AgentMiddleware[AgentState]):
    """在新可见用户轮次开始时建立当前 DataAgent 空闲快照。"""

    def __init__(self, config: DataQueryServiceAbilityConfig) -> None:
        """初始化轮次隔离 middleware。

        Args:
            config: DataAgent service ability 配置，用于绑定当前数据源。
        """
        super().__init__()
        self._config = config

    def _reset(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        """为新用户轮次创建 idle 状态，避免复用旧轮快照。

        Args:
            state: LangGraph 当前线程状态。
        Returns:
            需要写入状态的更新；当前轮次未变化时返回 None。
        """
        turn_id = current_visible_turn_id(state)
        if turn_id is None:
            return None
        active = get_active_service_state(state)
        if isinstance(active, Mapping) and active.get("turn_id") == turn_id:
            return None
        return {
            "service_states": [
                make_service_state(
                    turn_id=turn_id,
                    stage="idle",
                    data_source_id=self._config.data_source_id,
                )
            ]
        }

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """同步处理新用户轮次的 DataAgent 状态隔离。"""
        return self._reset(state)

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """异步处理新用户轮次的 DataAgent 状态隔离。"""
        return self._reset(state)
