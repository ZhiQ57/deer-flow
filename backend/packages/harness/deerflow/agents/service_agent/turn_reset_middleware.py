"""DataAgent 用户轮次隔离 middleware。"""

# ADD: DataAgent 正式查询闭环新增，新可见用户消息必须使旧标签授权和 SQL 快照失效。
from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, override

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


class DataAgentTurnResetMiddleware(AgentMiddleware):
    """在新用户轮次开始前清除旧查询快照的执行权限。"""

    # ADD: 保存能力配置中的服务端数据源 ID，状态重置不接受客户端覆盖。
    def __init__(self, config: DataQueryServiceAbilityConfig) -> None:
        """初始化用户轮次隔离 middleware。"""
        super().__init__()
        self._config = config

    @override
    def before_agent(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """检测新可见用户消息并将活动快照重置为 idle。"""
        active = get_active_service_state(state)
        turn_id = current_visible_turn_id(state)
        if active is None or turn_id is None or active.get("turn_id") == turn_id:
            return None
        snapshot_id = f"idle:sha256:{sha256(f'{turn_id}\n{self._config.data_source_id}'.encode()).hexdigest()}"
        return {
            "service_states": [
                make_service_state(
                    turn_id=turn_id,
                    stage="idle",
                    snapshot_id=snapshot_id,
                    data_source_id=self._config.data_source_id,
                    payload={},
                )
            ]
        }

    @override
    async def abefore_agent(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """异步运行时复用同步轮次隔离逻辑。"""
        return self.before_agent(state, runtime)
