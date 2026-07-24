"""DataAgent SQL Executor 与 SQL SubAgent 执行会话测试。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from deerflow.agents.service_agent.binding import resolve_data_source_binding
from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig
from deerflow.agents.service_agent.sql_executor import (
    SqlExecutionRequest,
    SqlExecutionService,
    SqlValidationRequest,
    _classify_execution_error,
)
from deerflow.agents.service_agent.sql_tools import build_sql_tools
from deerflow.agents.service_agent.state import build_retrieval_context
from deerflow.tools.builtins.task_tool import _build_data_query_sql_result_from_steps


def _config(*, max_execution_attempts: int = 3) -> DataQueryServiceAbilityConfig:
    """构造 SQL Executor 测试配置。

    Args:
        max_execution_attempts: 当前 SQL 子任务允许的数据库执行次数。

    Returns:
        已校验的 DataAgent 查询能力配置。
    """
    return DataQueryServiceAbilityConfig.model_validate(
        {
            "type": "data_query",
            "version": 1,
            "enable_sql_rag": True,
            "table_rag_config": "tablerag.yaml",
            "data_source_id": "sales-pg",
            "source_binding_mode": "same_physical_target",
            "confirmation_mode": "auto",
            "sql_subagent_name": "sql-subagent",
            "sql_execution": {
                "enabled": True,
                "database_type": "postgresql",
                "dsn_env": "DATA_AGENT_SQL_DSN",
                "readonly": True,
                "max_execution_attempts": max_execution_attempts,
                "max_rows": 10,
                "max_cell_chars": 200,
                "max_result_chars": 10_000,
                "allowed_schemas": ["public"],
                "allowed_tables": ["orders", "public.orders"],
                "allowed_columns": [
                    "region",
                    "missing_region",
                    "orders.region",
                    "orders.missing_region",
                ],
            },
        }
    )


def _retrieval(config: DataQueryServiceAbilityConfig) -> dict[str, Any]:
    """构造绑定当前数据库目标的 TableRAG 检索结果。

    Args:
        config: 当前 SQL 执行配置。

    Returns:
        带服务端绑定和字段 registry 的检索上下文。
    """
    binding = resolve_data_source_binding(config)
    return build_retrieval_context(
        {
            "ok": True,
            "operation": "hybrid-search",
            "result": {
                "tables": [{"table_name": "orders"}],
                "columns": [
                    {"table_name": "orders", "column_name": "region"},
                    {"table_name": "orders", "column_name": "missing_region"},
                ],
            },
        },
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id=config.data_source_id,
        binding=binding,
    )


@pytest.fixture(autouse=True)
def _database_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """为每个测试提供同物理目标的检索和执行 DSN。"""
    dsn = "postgresql://readonly:secret@db.local:5432/sales"
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", dsn)
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", dsn)


def test_sql_execution_service_validates_and_returns_json_safe_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """共享 Service 必须完成校验、只读执行和 JSON 安全结果转换。"""
    config = _config()
    service = SqlExecutionService(config)
    validation = service.validate(
        SqlValidationRequest(
            sql="SELECT orders.region FROM public.orders",
            retrieval=_retrieval(config),
            snapshot_id="snapshot-1",
        )
    )
    monkeypatch.setattr(
        "deerflow.agents.service_agent.sql_executor._execute_postgres",
        lambda sql, dsn, ability: (["region"], [("华东",)]),
    )

    result = service.execute(
        SqlExecutionRequest(
            sql=validation["executable_sql"],
            validation_digest=validation["validation_digest"],
            validation=validation,
        )
    )

    assert validation["valid"] is True
    assert result["ok"] is True
    assert result["database_type"] == "postgresql"
    assert result["columns"] == ["region"]
    assert result["rows"] == [{"region": "华东"}]
    assert "secret" not in json.dumps(result, ensure_ascii=False)


def test_sql_execution_service_supports_manual_ui_without_retrieval_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """前端手动执行必须复用只读和白名单校验，但不要求伪造 Query Snapshot。"""
    config = _config()
    service = SqlExecutionService(config)

    validation = service.validate(
        SqlValidationRequest(
            sql="SELECT orders.region FROM public.orders",
            source="manual_ui",
        )
    )
    monkeypatch.setattr(
        "deerflow.agents.service_agent.sql_executor._execute_postgres",
        lambda sql, dsn, ability: (["region"], [("华东",)]),
    )

    result = service.execute(
        SqlExecutionRequest(
            sql=validation["executable_sql"],
            validation_digest=validation["validation_digest"],
            validation=validation,
            source="manual_ui",
        )
    )

    assert validation["valid"] is True
    assert validation["source"] == "manual_ui"
    assert validation["snapshot_id"] is None
    assert result["ok"] is True
    assert result["rows"] == [{"region": "华东"}]


def test_sql_execution_service_rejects_cross_source_validation() -> None:
    """SubAgent 校验摘要不能被前端手动执行上下文复用。"""
    config = _config()
    service = SqlExecutionService(config)
    validation = service.validate(
        SqlValidationRequest(
            sql="SELECT orders.region FROM public.orders",
            retrieval=_retrieval(config),
            snapshot_id="snapshot-1",
        )
    )

    result = service.execute(
        SqlExecutionRequest(
            sql=validation["executable_sql"],
            validation_digest=validation["validation_digest"],
            validation=validation,
            source="manual_ui",
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "SQL_DIGEST_MISMATCH"


@pytest.mark.parametrize(
    ("sql", "error_code"),
    [
        ("DELETE FROM public.orders", "SQL_READONLY_REQUIRED"),
        ("SELECT orders.region FROM public.users", "SQL_TABLE_NOT_ALLOWED"),
    ],
)
def test_manual_ui_keeps_readonly_and_allowlist_guards(sql: str, error_code: str) -> None:
    """前端手动执行不能绕过只读和数据库对象白名单。"""
    validation = SqlExecutionService(_config()).validate(SqlValidationRequest(sql=sql, source="manual_ui"))

    assert validation["valid"] is False
    assert validation["error_code"] == error_code


def test_sql_execution_service_returns_repairable_database_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """数据库字段错误必须返回可供 SQL 模型修复的安全结构化信息。"""

    class UndefinedColumnError(Exception):
        """模拟 PostgreSQL undefined_column 异常。"""

        sqlstate = "42703"

        class diag:
            """模拟 psycopg 诊断字段。"""

            message_primary = 'column "missing_region" does not exist'
            column_name = "missing_region"

    config = _config()
    service = SqlExecutionService(config)
    validation = service.validate(
        SqlValidationRequest(
            sql="SELECT orders.missing_region FROM public.orders",
            retrieval=_retrieval(config),
            snapshot_id="snapshot-1",
        )
    )
    monkeypatch.setattr(
        "deerflow.agents.service_agent.sql_executor._execute_postgres",
        lambda sql, dsn, ability: (_ for _ in ()).throw(UndefinedColumnError()),
    )

    result = service.execute(
        SqlExecutionRequest(
            sql=validation["executable_sql"],
            validation_digest=validation["validation_digest"],
            validation=validation,
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "SQL_EXECUTION_FAILED"
    assert result["error_category"] == "unknown_column"
    assert result["retryable"] is True
    assert result["recommended_action"] == "repair_sql"
    assert result["error_message"] == 'column "missing_region" does not exist'
    assert "postgresql://" not in str(result)


@pytest.mark.parametrize(
    ("database_type", "error_args", "sqlstate", "expected_category", "expected_retryable", "expected_action"),
    [
        ("postgresql", ("syntax error near SELECT",), "42601", "syntax_error", True, "repair_sql"),
        ("postgresql", ("canceling statement due to statement timeout",), "57014", "timeout", True, "simplify_sql"),
        ("postgresql", ("connection reset",), "08006", "connection_error", True, "retry_same_sql"),
        ("mysql", (1054, "Unknown column 'missing_region' in 'field list'"), None, "unknown_column", True, "repair_sql"),
        ("mysql", (3024, "Query execution was interrupted"), None, "timeout", True, "simplify_sql"),
        ("mysql", (2003, "Can't connect to MySQL server"), None, "connection_error", True, "retry_same_sql"),
        ("mysql", (1142, "SELECT command denied"), None, "permission_denied", False, "stop"),
    ],
)
def test_sql_executor_classifies_postgres_and_mysql_errors(
    database_type: str,
    error_args: tuple[Any, ...],
    sqlstate: str | None,
    expected_category: str,
    expected_retryable: bool,
    expected_action: str,
) -> None:
    """PostgreSQL/MySQL 驱动错误必须映射为稳定的 SQL 修复决策。"""
    error = RuntimeError(*error_args)
    if sqlstate is not None:
        error.sqlstate = sqlstate  # type: ignore[attr-defined]

    result = _classify_execution_error(
        error,
        database_type,
        dsn="postgresql://readonly:super-secret@db.local:5432/sales",
    )

    assert result.category == expected_category
    assert result.retryable is expected_retryable
    assert result.recommended_action == expected_action
    assert "super-secret" not in str(result)


def test_sql_subagent_can_revalidate_repaired_sql_and_execute_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """首次执行失败后，SQL SubAgent 必须重新校验修复 SQL 才能再次执行。"""
    config = _config(max_execution_attempts=2)
    tools = build_sql_tools(
        config,
        {
            "snapshot_id": "snapshot-1",
            "payload": {
                "retrieval": _retrieval(config),
                "approval": {"status": "approved", "action": "execute"},
            },
        },
    )

    class UndefinedColumnError(Exception):
        """模拟首次 SQL 的字段错误。"""

        sqlstate = "42703"

        class diag:
            """模拟 psycopg 诊断字段。"""

            message_primary = 'column "missing_region" does not exist'
            column_name = "missing_region"

    calls = 0

    def execute_postgres(sql: str, dsn: str, ability: DataQueryServiceAbilityConfig) -> tuple[list[str], list[Any]]:
        """首次返回字段错误，第二次返回修复后的结果。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UndefinedColumnError
        return ["region"], [("华东",)]

    monkeypatch.setattr(
        "deerflow.agents.service_agent.sql_executor._execute_postgres",
        execute_postgres,
    )

    first_validation = json.loads(tools[0].invoke({"sql": "SELECT orders.missing_region FROM public.orders"}))
    first_execution = json.loads(
        tools[1].invoke(
            {
                "sql": first_validation["executable_sql"],
                "validation_digest": first_validation["validation_digest"],
            }
        )
    )
    repeated_without_validation = json.loads(
        tools[1].invoke(
            {
                "sql": first_validation["executable_sql"],
                "validation_digest": first_validation["validation_digest"],
            }
        )
    )
    repaired_validation = json.loads(tools[0].invoke({"sql": "SELECT orders.region FROM public.orders"}))
    repaired_execution = json.loads(
        tools[1].invoke(
            {
                "sql": repaired_validation["executable_sql"],
                "validation_digest": repaired_validation["validation_digest"],
            }
        )
    )

    assert first_execution["ok"] is False
    assert first_execution["error_category"] == "unknown_column"
    assert first_execution["attempt"] == 1
    assert repeated_without_validation["error_code"] == "SQL_EXECUTION_ALREADY_ATTEMPTED"
    assert repaired_execution["ok"] is True
    assert repaired_execution["attempt"] == 2
    assert repaired_execution["rows"] == [{"region": "华东"}]
    assert calls == 2

    authoritative = _build_data_query_sql_result_from_steps(
        [
            {"type": "tool", "name": "data_validate_sql", "content": json.dumps(first_validation, ensure_ascii=False)},
            {"type": "tool", "name": "data_execute_sql", "content": json.dumps(first_execution, ensure_ascii=False)},
            {"type": "tool", "name": "data_validate_sql", "content": json.dumps(repaired_validation, ensure_ascii=False)},
            {"type": "tool", "name": "data_execute_sql", "content": json.dumps(repaired_execution, ensure_ascii=False)},
        ],
        active_state={
            "snapshot_id": "snapshot-1",
            "data_source_id": config.data_source_id,
            "payload": {
                "retrieval": _retrieval(config),
                "approval": {"status": "approved", "action": "execute"},
            },
        },
        data_source_id=config.data_source_id,
    )
    assert authoritative is not None
    assert authoritative["execution"]["ok"] is True
    assert authoritative["execution"]["attempt"] == 2


def test_sql_subagent_execution_attempt_budget_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """SQL SubAgent 重新校验也不能突破配置的数据库执行次数。"""
    config = _config(max_execution_attempts=1)
    tools = build_sql_tools(
        config,
        {
            "snapshot_id": "snapshot-1",
            "payload": {
                "retrieval": _retrieval(config),
                "approval": {"status": "approved", "action": "execute"},
            },
        },
    )
    driver_calls = 0

    def fail_execution(sql: str, dsn: str, ability: DataQueryServiceAbilityConfig) -> tuple[list[str], list[Any]]:
        """记录驱动调用并返回通用执行错误。"""
        nonlocal driver_calls
        driver_calls += 1
        raise RuntimeError("database rejected query")

    monkeypatch.setattr(
        "deerflow.agents.service_agent.sql_executor._execute_postgres",
        fail_execution,
    )

    first_validation = json.loads(tools[0].invoke({"sql": "SELECT orders.region FROM public.orders"}))
    first_execution = json.loads(
        tools[1].invoke(
            {
                "sql": first_validation["executable_sql"],
                "validation_digest": first_validation["validation_digest"],
            }
        )
    )
    second_validation = json.loads(tools[0].invoke({"sql": "SELECT orders.region FROM public.orders LIMIT 1"}))
    second_execution = json.loads(
        tools[1].invoke(
            {
                "sql": second_validation["executable_sql"],
                "validation_digest": second_validation["validation_digest"],
            }
        )
    )

    assert first_execution["ok"] is False
    assert second_execution["error_code"] == "SQL_EXECUTION_ALREADY_ATTEMPTED"
    assert second_execution["attempt"] == 1
    assert second_execution["max_attempts"] == 1
    assert driver_calls == 1
