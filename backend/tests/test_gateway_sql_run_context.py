"""Gateway SQL Execution Run 上下文测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.gateway.modules.sql_execution.run_context import (
    prepare_sql_execution_run_context,
)
from app.gateway.modules.sql_execution.runtime_registry import sql_execution_runtime_registry


@pytest.mark.asyncio
async def test_prepare_run_context_injects_only_safe_binding() -> None:
    """真实 Secret 只能进入 Gateway 注册表，不能进入 Harness 运行绑定。"""
    config = {
        "context": {
            "thread_id": "thread-1",
            "user_id": "user-1",
            "agent_name": "data-agent",
            "secrets": {
                "database-dsn": "postgresql://readonly:super-secret@db.local/sales",
                "search-token": "keep-for-harness-skill",
            },
        }
    }
    binding = {
        "version": 1,
        "data_source_id": "sales-pg",
        "database_type": "postgresql",
        "binding_fingerprint": "sha256:binding",
    }

    with (
        patch(
            "app.gateway.modules.sql_execution.run_context._load_run_sql_binding",
            return_value=(binding, "secret://database-dsn"),
        ),
        patch.object(sql_execution_runtime_registry, "register_run") as register_run,
    ):
        await prepare_sql_execution_run_context(config, run_id="run-1")

    assert config["context"]["data_query_binding"] == binding
    assert "data_query_service_ability" not in config["context"]
    assert "super-secret" not in str(config["context"]["data_query_binding"])
    assert config["context"]["secrets"] == {
        "search-token": "keep-for-harness-skill",
    }
    register_run.assert_called_once()
    assert register_run.call_args.kwargs["secrets"] == {
        "database-dsn": "postgresql://readonly:super-secret@db.local/sales",
    }
    assert "secrets" not in register_run.call_args.kwargs["binding"]
