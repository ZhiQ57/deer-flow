"""DataAgent SQL Executor 与 SQL SubAgent 执行会话测试。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.gateway.modules.sql_execution.binding import resolve_data_source_binding
from app.gateway.modules.sql_execution.contracts import (
    SqlExecutionRequest,
    SqlValidationRequest,
)
from app.gateway.modules.sql_execution.error_classifier import classify_execution_error
from app.gateway.modules.sql_execution.service import SqlExecutionService
from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig


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
            },
        }
    )


def _binding(config: DataQueryServiceAbilityConfig) -> dict[str, Any]:
    """构造绑定当前数据库目标的无密钥运行绑定。

    Args:
        config: 当前 SQL 执行配置。

    Returns:
        Gateway 注入 Harness 的安全绑定摘要。
    """
    return resolve_data_source_binding(config)


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
            binding=_binding(config),
            snapshot_id="snapshot-1",
        )
    )
    monkeypatch.setattr(
        "app.gateway.modules.sql_execution.drivers.execute_postgres",
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
    """前端手动执行必须复用只读和 Schema 校验，但不要求伪造 Query Snapshot。"""
    config = _config()
    service = SqlExecutionService(config)

    validation = service.validate(
        SqlValidationRequest(
            sql="SELECT orders.region FROM public.orders",
            source="manual_ui",
        )
    )
    monkeypatch.setattr(
        "app.gateway.modules.sql_execution.drivers.execute_postgres",
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


def test_manual_ui_does_not_require_static_table_or_column_allowlist() -> None:
    """前端手动执行不能因缺少已删除的表/字段静态配置而拒绝 SQL。"""
    validation = SqlExecutionService(_config()).validate(
        SqlValidationRequest(
            sql="SELECT unknown_column FROM public.unknown_table",
            source="manual_ui",
        )
    )

    assert validation["valid"] is True
    assert "error_code" not in validation


def test_subagent_no_longer_requires_retrieval_registry() -> None:
    """SQL SubAgent 校验不再依赖 SQLRAG registry，字段错误交给数据库返回。"""
    config = _config()
    validation = SqlExecutionService(config).validate(
        SqlValidationRequest(
            sql="SELECT orders.unknown_column FROM public.orders",
            binding=_binding(config),
            snapshot_id="snapshot-1",
        )
    )

    assert validation["valid"] is True
    assert "error_code" not in validation


def test_sql_execution_service_rejects_cross_source_validation() -> None:
    """SubAgent 校验摘要不能被前端手动执行上下文复用。"""
    config = _config()
    service = SqlExecutionService(config)
    validation = service.validate(
        SqlValidationRequest(
            sql="SELECT orders.region FROM public.orders",
            binding=_binding(config),
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
        ("SELECT region FROM private.orders", "SQL_SCHEMA_NOT_ALLOWED"),
    ],
)
def test_manual_ui_keeps_readonly_and_schema_guards(sql: str, error_code: str) -> None:
    """前端手动执行不能绕过只读和 Schema 约束，但不依赖静态表/字段列表。"""
    validation = SqlExecutionService(_config()).validate(SqlValidationRequest(sql=sql, source="manual_ui"))

    assert validation["valid"] is False
    assert validation["error_code"] == error_code
    if error_code == "SQL_SCHEMA_NOT_ALLOWED":
        assert validation["error_message"] == "Schema `private` 未配置在 sql_execution.allowed_schemas 中。"


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
            binding=_binding(config),
            snapshot_id="snapshot-1",
        )
    )
    monkeypatch.setattr(
        "app.gateway.modules.sql_execution.drivers.execute_postgres",
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


def test_sql_execution_service_preserves_mysql_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MySQL 字段错误必须把驱动主错误传给前端和 SQL SubAgent。"""
    monkeypatch.setenv(
        "DATA_AGENT_SQL_DSN",
        "mysql+pymysql://readonly:secret@db.local:3306/sales",
    )
    monkeypatch.setenv(
        "TABLERAG_MCP_INDEX_DSN",
        "mysql+pymysql://readonly:secret@db.local:3306/sales",
    )
    payload = _config().model_dump(mode="python")
    payload["sql_execution"].update(
        {
            "database_type": "mysql",
            "allowed_schemas": ["sales"],
        }
    )
    config = DataQueryServiceAbilityConfig.model_validate(payload)
    service = SqlExecutionService(config)
    validation = service.validate(
        SqlValidationRequest(
            sql="SELECT orders.missing_region FROM sales.orders",
            source="manual_ui",
        )
    )

    class UnknownColumnError(Exception):
        """模拟 MySQL 1054 未知字段异常。"""

    monkeypatch.setattr(
        "app.gateway.modules.sql_execution.drivers.execute_mysql",
        lambda sql, dsn, ability: (_ for _ in ()).throw(UnknownColumnError(1054, "Unknown column 'orders.missing_region' in 'field list'")),
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
    assert result["error_category"] == "unknown_column"
    assert result["error_message"] == "Unknown column 'orders.missing_region' in 'field list'"


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

    result = classify_execution_error(
        error,
        database_type,
        dsn="postgresql://readonly:super-secret@db.local:5432/sales",
    )

    assert result.category == expected_category
    assert result.retryable is expected_retryable
    assert result.recommended_action == expected_action
    assert "super-secret" not in str(result)
    if database_type == "mysql" and error_args[0] == 1054:
        assert result.message == "Unknown column 'missing_region' in 'field list'"
