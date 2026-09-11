"""sql-execute MCP Server 单元测试。"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SERVER_PATH = Path(__file__).parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("sql_execute_mcp_server", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)


def test_load_config_and_create_server() -> None:
    """配置应能加载，且 MCP 暴露 sql_execute 工具。"""
    config = SERVER.load_config(SERVER_PATH.with_name("config.yaml"))

    assert config.database_type == "mysql"
    assert config.allowed_schemas == ("text2sql",)
    assert list(SERVER.create_server(config)._tool_manager._tools) == ["sql_execute"]


@pytest.mark.parametrize(
    ("sql", "expected_error"),
    [
        ("DELETE FROM text2sql.orders", "SQL_READONLY_REQUIRED"),
        ("SELECT 1; SELECT 2", "SQL_MULTIPLE_STATEMENTS"),
        ("SELECT * FROM other_schema.orders", "SQL_SCHEMA_NOT_ALLOWED"),
    ],
)
def test_normalize_sql_rejects_unsafe_queries(sql: str, expected_error: str) -> None:
    """只读、单语句和 schema 边界必须在 MCP Server 内生效。"""
    config = SERVER.load_config(SERVER_PATH.with_name("config.yaml"))

    executable_sql, error_code = SERVER.normalize_sql(sql, config)

    assert executable_sql == ""
    assert error_code == expected_error


def test_normalize_sql_adds_and_clamps_limit() -> None:
    """查询缺少 LIMIT 时应自动加上预算，过大 LIMIT 应被收敛。"""
    config = SERVER.load_config(SERVER_PATH.with_name("config.yaml"))

    sql_without_limit, error_without_limit = SERVER.normalize_sql("SELECT 1", config)
    sql_with_large_limit, error_with_large_limit = SERVER.normalize_sql("SELECT 1 LIMIT 1000", config)

    assert error_without_limit is None
    assert error_with_large_limit is None
    assert "LIMIT 100" in sql_without_limit
    assert "LIMIT 100" in sql_with_large_limit


class _FakeCursor:
    """模拟 PyMySQL 游标。"""

    description = [("id",), ("name",)]

    def __init__(self) -> None:
        self.executed: list[str] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def fetchmany(self, _: int) -> list[tuple[Any, ...]]:
        return [(1, "Alice"), (2, "Bob")]


class _FakeConnection:
    """模拟 PyMySQL 连接。"""

    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.rollback_called = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def rollback(self) -> None:
        self.rollback_called = True

    def close(self) -> None:
        self.closed = True


def test_execute_sql_returns_real_rows_and_wrapped_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """执行结果必须保留真实 rows，并提供给 Lead-Agent 的包装内容。"""
    config = SERVER.load_config(SERVER_PATH.with_name("config.yaml"))
    connection = _FakeConnection()
    fake_pymysql = ModuleType("pymysql")
    fake_pymysql.connect = lambda **_: connection  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
    monkeypatch.setenv("DATA_AGENT_MYSQL_DSN", "mysql://user:password@localhost/text2sql")

    result = SERVER.execute_sql("SELECT id, name FROM text2sql.users", config)

    assert result["ok"] is True
    assert result["rows"] == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    assert result["columns"] == ["id", "name"]
    assert result["row_count"] == 2
    assert result["returned_row_count"] == 2
    assert "当前执行SQL为:" in result["content"]
    assert "SQL结果为:" in result["content"]
    assert '"rows": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]' in result["content"]
    assert connection.rollback_called is True
    assert connection.closed is True


def test_execute_sql_reports_budget_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    """超出行预算时，结果应明确区分读取行数和返回行数。"""
    config = replace(SERVER.load_config(SERVER_PATH.with_name("config.yaml")), max_rows=1)
    connection = _FakeConnection()
    fake_pymysql = ModuleType("pymysql")
    fake_pymysql.connect = lambda **_: connection  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
    monkeypatch.setenv("DATA_AGENT_MYSQL_DSN", "mysql://user:password@localhost/text2sql")

    result = SERVER.execute_sql("SELECT id FROM text2sql.users", config)

    assert result["ok"] is True
    assert result["rows"] == [{"id": 1, "name": "Alice"}]
    assert result["row_count"] == 2
    assert result["returned_row_count"] == 1
    assert result["truncated"] is True


def test_execute_sql_reports_missing_dsn() -> None:
    """没有数据库凭据时不得尝试连接，返回稳定错误合同。"""
    config = SERVER.load_config(SERVER_PATH.with_name("config.yaml"))
    result = SERVER.execute_sql("SELECT 1", replace(config, dsn_env="MISSING_SQL_DSN"))

    assert result["ok"] is False
    assert result["error_code"] == "SQL_DSN_MISSING"
    assert result["rows"] == []
