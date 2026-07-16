"""DataAgent 查询确认回复处理 middleware。"""

# ADD: DataAgent 正式查询闭环新增，验证现有 human-input v1 隐藏回复并推进服务状态。
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
from .turn_reset_middleware import current_visible_turn_id


class QueryApprovalMiddleware(AgentMiddleware):
    """在每次新 run 开始时消费唯一 pending DataAgent 确认回复。"""

    # ADD: 保存冻结配置，使确认选项不能被前端响应覆盖。
    def __init__(self, config: DataQueryServiceAbilityConfig) -> None:
        """初始化确认 middleware。"""
        super().__init__()
        self._config = config

    @staticmethod
    def _latest_response(state: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """读取最新隐藏 human-input v1 response。"""
        messages = state.get("messages")
        if not isinstance(messages, Sequence):
            return None
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                response = read_human_input_response(message.additional_kwargs)
                if response is not None:
                    return response
        return None

    @staticmethod
    def _action(response: Mapping[str, Any]) -> str:
        """把 option/text response 归一化为服务动作。"""
        if response.get("response_kind") == "option":
            return str(response.get("option_id") or response.get("value") or "")
        return "modify"

    @override
    def before_agent(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """校验 pending request 并返回状态更新。

        Args:
            state: 当前 checkpoint 状态。
            runtime: LangGraph runtime，上下文仅用于调用链追踪。

        Returns:
            业务状态更新；没有 pending 回复时返回 None。
        """
        active = get_active_service_state(state)
        if active is None or active.get("stage") != "awaiting_confirmation":
            return None
        visible_turn_id = current_visible_turn_id(state)
        if visible_turn_id is not None and visible_turn_id != active.get("turn_id"):
            return None
        response = self._latest_response(state)
        if response is None or response.get("source") != "ask_clarification":
            return None
        payload = active.get("payload")
        approval = payload.get("approval") if isinstance(payload, Mapping) else None
        request = payload.get("approval_request") if isinstance(payload, Mapping) else None
        if not isinstance(approval, Mapping) or not isinstance(request, Mapping):
            return None
        if response.get("request_id") != request.get("request_id"):
            # ADD: 旧或伪造 request 只能被忽略，不能改变当前授权状态。
            return None

        action = self._action(response)
        turn_id = str(active.get("turn_id") or "")
        snapshot_id = str(active.get("snapshot_id") or "")
        if action in {"execute", "sql_only"}:
            next_approval = {**dict(approval), "status": "approved", "action": action, "source": "human"}
            next_payload = {**dict(payload), "approval": next_approval}
            return {
                "service_states": [
                    make_service_state(
                        turn_id=turn_id,
                        stage="approved",
                        snapshot_id=snapshot_id,
                        data_source_id=self._config.data_source_id,
                        payload=next_payload,
                    )
                ]
            }
        if action == "cancel":
            next_approval = {**dict(approval), "status": "cancelled", "action": "cancel", "source": "human"}
            return {
                "service_states": [
                    make_service_state(
                        turn_id=turn_id,
                        stage="cancelled",
                        snapshot_id=snapshot_id,
                        data_source_id=self._config.data_source_id,
                        payload={**dict(payload), "approval": next_approval},
                    )
                ]
            }

        # ADD: 自由文本是新的查询条件，清除旧快照的标签、授权和 SQL 投影。
        value = str(response.get("value") or "").strip()
        if not value:
            return None
        revision = sha256(f"{snapshot_id}\n{value}".encode()).hexdigest()[:24]
        return {
            "service_states": [
                make_service_state(
                    turn_id=turn_id,
                    stage="retrieving",
                    snapshot_id=f"revision:sha256:{revision}",
                    data_source_id=self._config.data_source_id,
                    payload={"revision_query": value, "retrieval_calls": 0},
                )
            ]
        }

    @override
    async def abefore_agent(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """异步运行时复用同步确认校验。"""
        return self.before_agent(state, runtime)
