"""Gateway SQL 手动执行 API 测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import sql_execution as sql_execution_router


def _service_ability(*, enabled: bool = True) -> dict:
    """构造 DataAgent 查询能力配置。"""
    return {
        "type": "data_query",
        "version": 1,
        "enable_sql_rag": True,
        "table_rag_config": "tablerag.yaml",
        "data_source_id": "sales-pg",
        "source_binding_mode": "same_physical_target",
        "confirmation_mode": "auto",
        "sql_subagent_name": "sql-subagent",
        "sql_execution": {
            "enabled": enabled,
            "database_type": "postgresql",
            "dsn_env": "DATA_AGENT_SQL_DSN",
            "readonly": True,
            "max_rows": 10,
            "max_cell_chars": 200,
            "max_result_chars": 10_000,
            "allowed_schemas": ["public"],
        },
    }


def _client(*, owner: bool = True) -> tuple[TestClient, User]:
    """构造带认证用户和线程所有权的 Router 测试客户端。"""
    user = User(email="sql@example.com", password_hash="x", system_role="user")
    app = make_authed_test_app(user_factory=lambda: user)
    app.include_router(sql_execution_router.router)
    app.state.thread_store.get = AsyncMock(
        return_value={
            "thread_id": "thread-1",
            "user_id": str(user.id) if owner else "other-user",
        }
    )
    return TestClient(app), user


def test_execute_sql_returns_raw_safe_result_for_owned_thread() -> None:
    """已登录用户可以在自己的 DataAgent 线程执行 SQL。"""
    client, user = _client()
    execution = {
        "version": 1,
        "ok": True,
        "database_type": "postgresql",
        "duration_ms": 12.5,
        "sql_sha256": "sha256:sql",
        "validation_digest": "sha256:validation",
        "row_count": 1,
        "returned_row_count": 1,
        "columns": ["region"],
        "rows": [{"region": "华东"}],
        "truncated": False,
        "empty": False,
    }
    service = SimpleNamespace(
        validate=lambda request: {
            "version": 1,
            "valid": True,
            "source": "manual_ui",
            "executable_sql": "SELECT orders.region FROM public.orders LIMIT 10",
            "sql_sha256": "sha256:sql",
            "validation_digest": "sha256:validation",
            "database_type": "postgresql",
            "binding_fingerprint": "sha256:binding",
            "snapshot_id": None,
        },
        aexecute=AsyncMock(return_value=execution),
    )

    with (
        patch.object(
            sql_execution_router,
            "load_agent_config",
            return_value=SimpleNamespace(service_ability=_service_ability()),
        ) as load_agent,
        patch.object(sql_execution_router, "SqlExecutionService", return_value=service),
    ):
        response = client.post(
            "/api/threads/thread-1/sql/execute",
            json={
                "agent_name": "data-agent",
                "sql": "SELECT orders.region FROM public.orders",
                "source": "manual_ui",
            },
        )

    assert response.status_code == 200
    assert response.json()["rows"] == [{"region": "华东"}]
    assert "dsn" not in response.text.lower()
    load_agent.assert_called_once_with("data-agent", user_id=str(user.id))
    service.aexecute.assert_awaited_once()


def test_execute_sql_returns_validation_error_without_calling_driver() -> None:
    """非只读 SQL 必须在数据库驱动调用前返回稳定校验错误。"""
    client, _ = _client()
    service = SimpleNamespace(
        validate=lambda request: {
            "version": 1,
            "valid": False,
            "error_code": "SQL_SCHEMA_NOT_ALLOWED",
            "error_message": "Schema `private` 未配置在 sql_execution.allowed_schemas 中。",
        },
        aexecute=AsyncMock(),
    )

    with (
        patch.object(
            sql_execution_router,
            "load_agent_config",
            return_value=SimpleNamespace(service_ability=_service_ability()),
        ),
        patch.object(sql_execution_router, "SqlExecutionService", return_value=service),
    ):
        response = client.post(
            "/api/threads/thread-1/sql/execute",
            json={"agent_name": "data-agent", "sql": "DELETE FROM orders", "source": "manual_ui"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == "SQL_SCHEMA_NOT_ALLOWED"
    assert response.json()["error_category"] == "validation_error"
    assert response.json()["error_message"] == "Schema `private` 未配置在 sql_execution.allowed_schemas 中。"
    service.aexecute.assert_not_awaited()


def test_execute_sql_rejects_thread_without_exact_owner() -> None:
    """SQL 高信任入口必须拒绝其他用户及旧的空所有者线程。"""
    client, _ = _client(owner=False)

    response = client.post(
        "/api/threads/thread-1/sql/execute",
        json={"agent_name": "data-agent", "sql": "SELECT 1", "source": "manual_ui"},
    )

    assert response.status_code == 404


def test_execute_sql_rejects_non_data_agent_and_disabled_executor() -> None:
    """普通 Agent 和关闭 SQL Executor 的 DataAgent 不能访问执行入口。"""
    client, _ = _client()

    with patch.object(
        sql_execution_router,
        "load_agent_config",
        return_value=SimpleNamespace(service_ability=None),
    ):
        ordinary = client.post(
            "/api/threads/thread-1/sql/execute",
            json={"agent_name": "lead-agent", "sql": "SELECT 1", "source": "manual_ui"},
        )

    with patch.object(
        sql_execution_router,
        "load_agent_config",
        return_value=SimpleNamespace(service_ability=_service_ability(enabled=False)),
    ):
        disabled = client.post(
            "/api/threads/thread-1/sql/execute",
            json={"agent_name": "data-agent", "sql": "SELECT 1", "source": "manual_ui"},
        )

    assert ordinary.status_code == 403
    assert disabled.status_code == 403


def test_execute_sql_rejects_missing_agent_and_invalid_source() -> None:
    """不存在的 Agent 和非前端来源必须返回稳定 HTTP 错误。"""
    client, _ = _client()

    with patch.object(sql_execution_router, "load_agent_config", return_value=None):
        missing = client.post(
            "/api/threads/thread-1/sql/execute",
            json={"agent_name": "missing", "sql": "SELECT 1", "source": "manual_ui"},
        )
    invalid_source = client.post(
        "/api/threads/thread-1/sql/execute",
        json={"agent_name": "data-agent", "sql": "SELECT 1", "source": "subagent"},
    )
    invalid_agent_name = client.post(
        "/api/threads/thread-1/sql/execute",
        json={"agent_name": "../data-agent", "sql": "SELECT 1", "source": "manual_ui"},
    )

    assert missing.status_code == 404
    assert invalid_source.status_code == 422
    assert invalid_agent_name.status_code == 422
