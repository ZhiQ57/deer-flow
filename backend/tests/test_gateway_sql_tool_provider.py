"""Gateway SQL SubAgent 工具提供器测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app.gateway.modules.sql_execution.runtime_registry import (
    SqlExecutionRuntimeRegistry,
    sql_execution_runtime_registry,
)
from app.gateway.modules.sql_execution.tool_provider import GatewaySqlSubagentToolProvider
from deerflow.subagents.tool_provider import SubagentToolContext


def _ability() -> dict:
    """构造 DataAgent 脱敏运行能力投影。

    Returns:
        合法的 DataAgent 配置字典。
    """
    return {
        "type": "data_query",
        "version": 1,
        "enable_sql_rag": True,
        "data_source_id": "sales-pg",
        "source_binding_mode": "same_physical_target",
        "confirmation_mode": "auto",
        "sql_subagent_name": "sql-subagent",
        "database_type": "postgresql",
        "sql_execution_enabled": True,
    }


def _context(*, action: str = "execute") -> SubagentToolContext:
    """构造 SQL SubAgent 工具上下文。

    Args:
        action: 当前审批动作。

    Returns:
        带 approved Snapshot 的工具上下文。
    """
    return SubagentToolContext(
        subagent_type="sql-subagent",
        thread_id="thread-1",
        run_id="run-1",
        user_id="user-1",
        agent_name="data-agent",
        parent_context={
            "data_query_service_ability": _ability(),
            "data_query_sql_subagent_allowed": True,
        },
        parent_state={
            "service_states": [
                {
                    "service_name": "data_query",
                    "stage": "approved",
                    "snapshot_id": "snapshot-1",
                    "data_source_id": "sales-pg",
                    "payload": {
                        "approval": {"status": "approved", "action": action},
                    },
                }
            ]
        },
    )


@pytest.fixture(autouse=True)
def _runtime_registry() -> None:
    """为每个测试隔离 Gateway SQL Run 能力。"""
    sql_execution_runtime_registry.clear()
    sql_execution_runtime_registry.register_run(
        run_id="run-1",
        thread_id="thread-1",
        user_id="user-1",
        agent_name="data-agent",
        binding={"binding_fingerprint": "sha256:binding"},
        secrets={"database-dsn": "postgresql://readonly:secret@db.local/sales"},
    )
    yield
    sql_execution_runtime_registry.clear()


def test_provider_builds_gateway_tools_without_internal_arguments() -> None:
    """SQL SubAgent 只能提交 SQL，内部身份和 Secret 不进入工具参数。"""
    provider = GatewaySqlSubagentToolProvider(FastAPI())

    tools = provider.build_tools(_context())

    assert [tool.name for tool in tools] == ["data_validate_sql", "data_execute_sql"]
    assert tools[0].args_schema.model_json_schema()["required"] == ["sql"]
    assert tools[1].args_schema.model_json_schema()["required"] == [
        "sql",
        "validation_digest",
    ]
    schema_text = str(tools[1].args_schema.model_json_schema()).lower()
    assert "run_id" not in schema_text
    assert "agent_name" not in schema_text
    assert "secret" not in schema_text


def test_provider_keeps_sql_only_without_execution_tool() -> None:
    """sql_only Snapshot 只能获得 Gateway 校验工具。"""
    tools = GatewaySqlSubagentToolProvider(FastAPI()).build_tools(_context(action="sql_only"))

    assert [tool.name for tool in tools] == ["data_validate_sql"]


@pytest.mark.asyncio
async def test_provider_injects_gateway_identity_outside_model_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway 工具闭包必须自行注入 thread、run、user 和 Agent 身份。"""
    provider = GatewaySqlSubagentToolProvider(FastAPI())
    expected = {
        "version": 1,
        "kind": "data_query_sql_result",
        "snapshot_id": "snapshot-1",
        "data_source_id": "sales-pg",
        "validation": {"version": 1, "valid": True},
        "execution": None,
    }
    post = AsyncMock(return_value=expected)
    monkeypatch.setattr(provider, "_post", post)
    execute_tool = provider.build_tools(_context())[1]

    content, artifact = await execute_tool.coroutine(
        sql="SELECT 1",
        validation_digest="sha256:validation",
    )

    assert artifact == expected
    assert "data_query_sql_result" in content
    post.assert_awaited_once()
    kwargs = post.await_args.kwargs
    assert kwargs["context"].thread_id == "thread-1"
    assert kwargs["context"].run_id == "run-1"
    assert kwargs["context"].user_id == "user-1"
    assert kwargs["context"].agent_name == "data-agent"
    assert kwargs["sql"] == "SELECT 1"
    assert kwargs["validation_digest"] == "sha256:validation"


def test_runtime_registry_requires_new_validation_before_each_attempt() -> None:
    """Gateway 注册表必须消费校验代次、限制执行次数并在成功后停止。"""
    registry = SqlExecutionRuntimeRegistry()
    registry.register_run(
        run_id="run-1",
        thread_id="thread-1",
        user_id="user-1",
        agent_name="data-agent",
        binding={},
        secrets={},
    )
    assert registry.authorize_snapshot(
        run_id="run-1",
        thread_id="thread-1",
        user_id="user-1",
        agent_name="data-agent",
        snapshot_id="snapshot-1",
    )

    assert registry.record_validation(
        run_id="run-1",
        snapshot_id="snapshot-1",
        validation_digest="sha256:validation",
    )
    assert registry.reserve_attempt(
        run_id="run-1",
        snapshot_id="snapshot-1",
        validation_digest="sha256:validation",
        max_attempts=3,
    ) == (1, None)
    assert registry.reserve_attempt(
        run_id="run-1",
        snapshot_id="snapshot-1",
        validation_digest="sha256:validation",
        max_attempts=3,
    ) == (1, "validation_reuse")
    assert registry.record_validation(
        run_id="run-1",
        snapshot_id="snapshot-1",
        validation_digest="sha256:validation",
    )
    assert registry.reserve_attempt(
        run_id="run-1",
        snapshot_id="snapshot-1",
        validation_digest="sha256:validation",
        max_attempts=3,
    ) == (2, None)
    registry.mark_succeeded(run_id="run-1", snapshot_id="snapshot-1")
    assert registry.reserve_attempt(
        run_id="run-1",
        snapshot_id="snapshot-1",
        validation_digest="sha256:validation",
        max_attempts=3,
    ) == (2, "execution_complete")
    registry.discard_run("run-1")
    assert (
        registry.resolve(
            run_id="run-1",
            thread_id="thread-1",
            user_id="user-1",
            agent_name="data-agent",
            snapshot_id="snapshot-1",
        )
        is None
    )


def test_provider_fails_closed_without_gateway_run_capability() -> None:
    """未登记 Gateway Run 能力时不得向 SQL SubAgent 注入工具。"""
    sql_execution_runtime_registry.clear()

    assert GatewaySqlSubagentToolProvider(FastAPI()).build_tools(_context()) == []
