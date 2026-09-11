"""Gateway SQL 路由与模型侧 MCP 边界回归测试。"""

from __future__ import annotations

from fastapi import FastAPI

from app.gateway.modules.sql_execution.tool_provider import GatewaySqlSubagentToolProvider
from deerflow.subagents.tool_provider import SubagentToolContext


def _context() -> SubagentToolContext:
    """构造没有 Gateway SQL 能力注入的普通子代理上下文。"""
    return SubagentToolContext(
        subagent_type="sql-subagent",
        thread_id="thread-1",
        run_id="run-1",
        user_id="user-1",
        agent_name="data-agent",
        parent_context={},
        parent_state={},
        task_prompt="生成 SQL",
    )


def test_legacy_gateway_provider_is_not_registered() -> None:
    """模型侧不再注册旧的 Gateway SQL Tool Provider。"""
    import app.gateway.app as gateway_app

    assert not hasattr(gateway_app, "register_gateway_sql_tool_provider")


def test_legacy_gateway_provider_fails_closed_without_injected_capability() -> None:
    """即使旧提供器模块仍存在，也不能在没有能力上下文时暴露工具。"""
    provider = GatewaySqlSubagentToolProvider(FastAPI())

    assert provider.build_tools(_context()) == []
