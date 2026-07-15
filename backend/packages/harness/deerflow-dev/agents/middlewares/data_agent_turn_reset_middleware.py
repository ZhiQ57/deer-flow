"""DataAgent 新用户轮次状态重置 middleware。"""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from agents.middlewares._data_agent_messages import is_visible_user_message


class DataAgentTurnResetMiddleware(AgentMiddleware):
    """仅在新真实用户消息进入时重置 DataAgent 单轮流程状态。"""

    @staticmethod
    def _reset(state: dict[str, Any]) -> dict[str, Any] | None:
        """为新用户轮次清理旧的数据流程状态。

        Args:
            state: 当前 LangGraph 状态。

        Return:
            新用户轮次的状态更新；非新用户消息返回 None。
        """
        messages = list(state.get("messages") or [])
        if not messages or not is_visible_user_message(messages[-1]):
            return None
        return {
            "data_agent_stage": None,
            "data_query_context": None,
            "data_query_labels": None,
            "data_retrieval_context": None,
            "data_generated_sql": None,
            "data_sql_validation": None,
            "data_sql_execution": None,
            "data_last_successful_sql_execution": None,
            "data_chart_spec": None,
        }

    @override
    def before_agent(self, state, runtime: Runtime) -> dict[str, Any] | None:
        """同步重置新用户轮次状态。

        Args:
            state: 当前 LangGraph 状态。
            runtime: LangGraph 运行时。

        Return:
            状态更新或 None。
        """
        return self._reset(state)

    @override
    async def abefore_agent(self, state, runtime: Runtime) -> dict[str, Any] | None:
        """异步重置新用户轮次状态。

        Args:
            state: 当前 LangGraph 状态。
            runtime: LangGraph 运行时。

        Return:
            状态更新或 None。
        """
        return self._reset(state)
