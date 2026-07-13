from __future__ import annotations

import sys
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """从移动后的测试目录定位仓库根目录。

    Args:
        无。

    Return:
        DeerFlow 仓库根目录。
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "AGENTS.md").is_file() and (candidate / "backend" / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("无法定位 DeerFlow 仓库根目录。")


REPO_ROOT = _repo_root()
DEV_PATH = REPO_ROOT / "backend" / "packages" / "harness" / "deerflow-dev"
if str(DEV_PATH) not in sys.path:
    sys.path.insert(0, str(DEV_PATH))

from tools.builtins.chart_spec_tool import normalize_chart_y  # noqa: E402
from tools.builtins.data_execute_sql_tool import data_execute_sql_tool  # noqa: E402
from tools.builtins.data_validate_sql_tool import data_validate_sql_tool  # noqa: E402
from tools.chart_spec import build_chart_spec  # noqa: E402
from tools.database import MySQLExecutionSettings, execute_readonly_sql, format_safe_database_error  # noqa: E402
from tools.sql_validation import SQLValidationError, sql_sha256, validate_readonly_sql  # noqa: E402


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE users SET name = 'x'",
        "DELETE FROM users",
        "DROP TABLE users",
        "SELECT 1; SELECT 2",
        "SELECT * FROM users FOR UPDATE",
        "SELECT * FROM users INTO OUTFILE '/tmp/a.csv'",
        "SELECT SLEEP(10)",
        "SELECT * FROM users WHERE id = @user_id",
        "SELECT /*+ MAX_EXECUTION_TIME(999999) */ * FROM users",
        "SELECT /*!50000 SLEEP(10) */ 1",
    ],
)
def test_validate_readonly_sql_rejects_unsafe_statements(sql: str) -> None:
    """校验危险 SQL 会被拒绝。

    Args:
        sql: 待校验 SQL。

    Return:
        None。
    """
    with pytest.raises(SQLValidationError):
        validate_readonly_sql(sql, max_rows=100)


def test_validate_readonly_sql_allows_cte_and_adds_limit() -> None:
    """校验只读 CTE 被允许且自动添加结果上限。

    Args:
        无。

    Return:
        None。
    """
    result = validate_readonly_sql("WITH recent AS (SELECT id FROM users) SELECT id FROM recent", max_rows=100)

    assert result.valid is True
    assert result.limit_applied is True
    assert result.max_rows == 100
    assert "LIMIT 100" in result.executable_sql.upper()
    assert result.sql_sha256


def test_validate_readonly_sql_clamps_existing_limit() -> None:
    """校验过大的 LIMIT 会被收紧。

    Args:
        无。

    Return:
        None。
    """
    result = validate_readonly_sql("SELECT id FROM users LIMIT 1000", max_rows=50)

    assert "LIMIT 50" in result.executable_sql.upper()
    assert result.limit_applied is True
    assert result.effective_limit == 50


def test_validate_readonly_sql_rejects_cross_database_and_system_schema() -> None:
    """校验 SQL 不能越过配置的业务数据库边界。

    Args:
        无。

    Return:
        None。
    """
    with pytest.raises(SQLValidationError, match="跨库"):
        validate_readonly_sql(
            "SELECT * FROM other_db.users",
            max_rows=100,
            allowed_database="text2sql",
        )
    with pytest.raises(SQLValidationError, match="系统数据库"):
        validate_readonly_sql(
            "SELECT * FROM information_schema.tables",
            max_rows=100,
            allowed_database="text2sql",
        )


def test_mysql_settings_parse_dsn_and_redact_password() -> None:
    """校验 MySQL DSN 解析和日志脱敏。

    Args:
        无。

    Return:
        None。
    """
    settings = MySQLExecutionSettings.from_env(
        {
            "DATA_AGENT_MYSQL_DSN": "mysql+pymysql://reporter:test%40password@db.internal:3308/analytics?charset=utf8mb4",
            "DATA_AGENT_SQL_MAX_ROWS": "25",
            "DATA_AGENT_SQL_TIMEOUT_MS": "3000",
        }
    )

    assert settings.host == "db.internal"
    assert settings.port == 3308
    assert settings.user == "reporter"
    assert settings.password == "test@password"
    assert settings.database == "analytics"
    assert settings.max_rows == 25
    assert settings.query_timeout_ms == 3000
    assert "test@password" not in settings.safe_description()
    assert "***" in settings.safe_description()


def test_database_error_formatter_redacts_raw_and_encoded_passwords() -> None:
    """校验数据库异常返回模型前会脱敏明文和 URL 编码密码。

    Args:
        无。

    Return:
        None。
    """
    settings = MySQLExecutionSettings(
        host="db.internal",
        port=3308,
        user="reporter",
        password="test@password",
        database="analytics",
    )
    exc = RuntimeError("connect mysql+pymysql://reporter:test%40password@db.internal/analytics failed; password=test@password")

    result = format_safe_database_error(exc, settings=settings)

    assert "test@password" not in result
    assert "test%40password" not in result
    assert result.count("***") >= 2


def test_sql_validation_tool_preserves_last_execution_until_replacement_runs(monkeypatch) -> None:
    """校验新 SQL 仅完成校验时不会清除上一条可用执行结果。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    monkeypatch.setenv("DATA_AGENT_MYSQL_HOST", "db.internal")
    monkeypatch.setenv("DATA_AGENT_MYSQL_USER", "reporter")
    monkeypatch.setenv("DATA_AGENT_MYSQL_PASSWORD", "test-password")
    monkeypatch.setenv("DATA_AGENT_MYSQL_DATABASE", "analytics")
    runtime = type("Runtime", (), {"state": {"data_sql_execution": {"ok": True, "sql_sha256": "old"}}})()

    result = data_validate_sql_tool.func(runtime, "SELECT 1", "call-1")

    assert "data_sql_execution" not in result.update
    assert result.update["data_sql_validation"]["valid"] is True


def test_sql_execution_failure_preserves_last_successful_result(monkeypatch) -> None:
    """校验替代 SQL 执行失败时不会清除上一条成功结果。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    tools_module = import_module("tools.builtins.data_execute_sql_tool")

    monkeypatch.setenv("DATA_AGENT_MYSQL_HOST", "db.internal")
    monkeypatch.setenv("DATA_AGENT_MYSQL_USER", "reporter")
    monkeypatch.setenv("DATA_AGENT_MYSQL_PASSWORD", "test-password")
    monkeypatch.setenv("DATA_AGENT_MYSQL_DATABASE", "analytics")
    sql = "SELECT 1"
    previous = {"ok": True, "sql_sha256": "old", "rows": [{"value": 1}]}
    runtime = type(
        "Runtime",
        (),
        {
            "state": {
                "data_sql_validation": {"valid": True, "sql_sha256": sql_sha256(sql)},
                "data_last_successful_sql_execution": previous,
            }
        },
    )()
    monkeypatch.setattr(tools_module, "execute_readonly_sql", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("query failed")))

    result = data_execute_sql_tool.func(runtime, sql, "call-1")

    assert result.update["data_agent_stage"] == "sql_execution_failed"
    assert result.update["data_sql_execution"]["ok"] is False
    assert "data_last_successful_sql_execution" not in result.update


def test_sql_execution_reports_invalid_environment_without_unbound_error(monkeypatch) -> None:
    """校验 MySQL 配置解析失败时返回受控工具错误。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    monkeypatch.delenv("DATA_AGENT_MYSQL_DSN", raising=False)
    monkeypatch.delenv("DATA_AGENT_MYSQL_HOST", raising=False)
    monkeypatch.setenv("DATA_AGENT_MYSQL_USER", "reporter")
    monkeypatch.setenv("DATA_AGENT_MYSQL_DATABASE", "analytics")
    sql = "SELECT 1"
    runtime = type(
        "Runtime",
        (),
        {"state": {"data_sql_validation": {"valid": True, "sql_sha256": sql_sha256(sql)}}},
    )()

    result = data_execute_sql_tool.func(runtime, sql, "call-1")

    assert result.update["data_agent_stage"] == "sql_execution_failed"
    assert result.update["data_sql_execution"]["ok"] is False
    assert result.update["data_sql_execution"]["error"].startswith("ValueError:")
    assert "host" in result.update["data_sql_execution"]["error"]


def test_sql_execution_success_updates_last_successful_result(monkeypatch) -> None:
    """校验成功执行会同步刷新最后成功结果快照。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    tools_module = import_module("tools.builtins.data_execute_sql_tool")

    monkeypatch.setenv("DATA_AGENT_MYSQL_HOST", "db.internal")
    monkeypatch.setenv("DATA_AGENT_MYSQL_USER", "reporter")
    monkeypatch.setenv("DATA_AGENT_MYSQL_PASSWORD", "test-password")
    monkeypatch.setenv("DATA_AGENT_MYSQL_DATABASE", "analytics")
    sql = "SELECT 1"
    execution = {
        "ok": True,
        "sql_sha256": sql_sha256(sql),
        "executable_sql": sql,
        "columns": ["value"],
        "rows": [{"value": 1}],
        "row_count": 1,
        "truncated": False,
        "elapsed_ms": 1,
    }
    runtime = type(
        "Runtime",
        (),
        {"state": {"data_sql_validation": {"valid": True, "sql_sha256": sql_sha256(sql)}}},
    )()
    monkeypatch.setattr(tools_module, "execute_readonly_sql", lambda *args, **kwargs: execution)

    result = data_execute_sql_tool.func(runtime, sql, "call-1")

    assert result.update["data_agent_stage"] == "sql_executed"
    assert result.update["data_last_successful_sql_execution"] is execution


class _FakeCursor:
    """模拟 PyMySQL 游标。"""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.description = [("amount",), ("created_at",), ("note",)]

    def execute(self, sql: str) -> None:
        self.statements.append(sql)

    def fetchmany(self, size: int):
        assert size == 3
        return [
            {"amount": Decimal("12.30"), "created_at": datetime(2026, 7, 10, 8, 30), "note": "正常"},
            {"amount": Decimal("9.10"), "created_at": date(2026, 7, 9), "note": "x" * 30},
            {"amount": Decimal("1"), "created_at": date(2026, 7, 8), "note": "overflow"},
        ]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeConnection:
    """模拟 PyMySQL 连接。"""

    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_execute_readonly_sql_enforces_session_and_result_limits() -> None:
    """校验执行层设置只读事务并限制返回结果。

    Args:
        无。

    Return:
        None。
    """
    settings = MySQLExecutionSettings(
        host="127.0.0.1",
        port=3308,
        user="readonly",
        password="secret",
        database="text2sql",
        max_rows=2,
        query_timeout_ms=2000,
        max_cell_chars=10,
        max_result_chars=1000,
    )
    connection = _FakeConnection()

    result = execute_readonly_sql(
        "SELECT amount, created_at, note FROM report",
        settings=settings,
        connect_factory=lambda **kwargs: connection,
    )

    assert result["ok"] is True
    assert result["row_count"] == 2
    assert result["truncated"] is True
    assert result["rows"][0]["amount"] == "12.30"
    assert result["rows"][0]["created_at"] == "2026-07-10T08:30:00"
    assert result["rows"][1]["note"].endswith("...")
    assert connection.cursor_instance.statements[0] == "SET SESSION TRANSACTION READ ONLY"
    assert connection.cursor_instance.statements[1] == "SET SESSION MAX_EXECUTION_TIME = 2000"
    assert connection.cursor_instance.statements[2] == "START TRANSACTION READ ONLY"
    assert connection.rolled_back is True
    assert connection.closed is True


def test_build_chart_spec_infers_line_chart_from_date_and_numeric_columns() -> None:
    """校验 ChartSpec 能从日期和数值列推断折线图。

    Args:
        无。

    Return:
        None。
    """
    execution = {
        "ok": True,
        "columns": ["day", "total"],
        "rows": [
            {"day": "2026-07-09", "total": 10},
            {"day": "2026-07-10", "total": 12},
        ],
        "row_count": 2,
        "truncated": False,
    }

    spec = build_chart_spec(execution, chart_type="auto", title="每日数量")

    assert spec["type"] == "line"
    assert spec["x"] == "day"
    assert spec["y"] == ["total"]
    assert spec["title"] == "每日数量"


def test_build_chart_spec_rejects_non_numeric_y_for_chart() -> None:
    """校验非表格图表不能把文本列作为数值轴。

    Args:
        无。

    Return:
        None。
    """
    execution = {
        "ok": True,
        "columns": ["name", "status"],
        "rows": [{"name": "甲", "status": "完成"}],
        "row_count": 1,
        "truncated": False,
    }

    with pytest.raises(ValueError, match="Y 轴必须是数值字段"):
        build_chart_spec(execution, chart_type="bar", x="name", y=["status"])


def test_build_chart_spec_rejects_non_finite_numeric_values() -> None:
    """校验 NaN 和 Infinity 不会被当作可绘图数值。

    Args:
        无。

    Return:
        None。
    """
    execution = {
        "ok": True,
        "columns": ["name", "value"],
        "rows": [{"name": "甲", "value": "NaN"}],
        "row_count": 1,
        "truncated": False,
    }

    with pytest.raises(ValueError, match="Y 轴必须是数值字段"):
        build_chart_spec(execution, chart_type="bar", x="name", y=["value"])


def test_chart_tool_normalizes_json_string_y_fields() -> None:
    """校验模型把 Y 轴列表编码为 JSON 字符串时仍可正常处理。

    Args:
        无。

    Return:
        None。
    """
    assert normalize_chart_y('["count", "amount"]') == ["count", "amount"]
    assert normalize_chart_y("count, amount") == ["count", "amount"]


def test_execute_readonly_sql_enforces_total_result_budget_on_first_row() -> None:
    """校验第一行超出总字符预算时也不会突破硬上限。

    Args:
        无。

    Return:
        None。
    """
    settings = MySQLExecutionSettings(
        host="127.0.0.1",
        port=3308,
        user="readonly",
        password="secret",
        database="text2sql",
        max_rows=2,
        query_timeout_ms=2000,
        max_cell_chars=100,
        max_result_chars=10,
    )
    connection = _FakeConnection()

    result = execute_readonly_sql(
        "SELECT amount, created_at, note FROM report",
        settings=settings,
        connect_factory=lambda **kwargs: connection,
    )

    assert result["rows"] == []
    assert result["row_count"] == 0
    assert result["truncated"] is True
