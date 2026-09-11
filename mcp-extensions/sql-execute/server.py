"""只读 SQL Execute MCP Server。"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, replace
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml
from mcp.server.fastmcp import FastMCP
from sqlglot import exp, parse


@dataclass(frozen=True, slots=True)
class SqlServerConfig:
    """SQL MCP Server 运行配置。"""

    name: str
    transport: str
    host: str
    port: int
    database_type: str
    dsn_env: str
    readonly: bool
    statement_timeout_seconds: int
    max_rows: int
    max_cell_chars: int
    max_result_chars: int
    allowed_schemas: tuple[str, ...]


def load_config(path: str | Path) -> SqlServerConfig:
    """从扩展目录 YAML 加载 SQL Server 配置。"""
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    server = raw.get("server") if isinstance(raw.get("server"), dict) else {}
    database = raw.get("database") if isinstance(raw.get("database"), dict) else {}
    database_type = str(database.get("type") or "postgresql").strip().lower().replace("-", "_")
    if database_type in {"postgres", "pg"}:
        database_type = "postgresql"
    if database_type not in {"mysql", "postgresql"}:
        raise ValueError("database.type 只支持 mysql 或 postgresql")
    allowed_schemas = tuple(str(item).strip() for item in (database.get("allowed_schemas") or []) if str(item).strip())
    if not allowed_schemas:
        raise ValueError("database.allowed_schemas 不能为空")
    dsn_env = str(database.get("dsn_env") or "").strip()
    if not dsn_env:
        raise ValueError("database.dsn_env 不能为空")
    readonly = bool(database.get("readonly", True))
    if readonly is not True:
        raise ValueError("database.readonly 必须为 true")
    return SqlServerConfig(
        name=str(server.get("name") or "sql-execute"),
        transport=str(server.get("transport") or "stdio"),
        host=str(server.get("host") or "127.0.0.1"),
        port=int(server.get("port") or 8003),
        database_type=database_type,
        dsn_env=dsn_env,
        readonly=readonly,
        statement_timeout_seconds=max(1, int(database.get("statement_timeout_seconds") or 10)),
        max_rows=max(1, int(database.get("max_rows") or 100)),
        max_cell_chars=max(100, int(database.get("max_cell_chars") or 2000)),
        max_result_chars=max(1000, int(database.get("max_result_chars") or 100000)),
        allowed_schemas=allowed_schemas,
    )


def _dialect(config: SqlServerConfig) -> str:
    """返回 sqlglot 方言名称。"""
    return "mysql" if config.database_type == "mysql" else "postgres"


def normalize_sql(sql: str, config: SqlServerConfig) -> tuple[str, str | None]:
    """校验并规范化单条只读 SQL。

    AgentLoop 不参与 SQL 业务校验；这里是 MCP Server 的数据边界，确保服务本身
    不会因为调用方换成 Lead-Agent 或 SubAgent 就失去只读约束。
    """
    if not isinstance(sql, str) or not sql.strip():
        return "", "SQL_EMPTY"
    try:
        statements = [item for item in parse(sql, read=_dialect(config)) if item is not None]
    except Exception:
        return "", "SQL_PARSE_ERROR"
    if len(statements) != 1:
        return "", "SQL_MULTIPLE_STATEMENTS"
    statement = statements[0]
    if not isinstance(statement, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return "", "SQL_READONLY_REQUIRED"
    if any(isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Transaction)) for node in statement.walk()):
        return "", "SQL_READONLY_REQUIRED"
    for table in statement.find_all(exp.Table):
        db = table.db
        catalog = table.catalog
        schema = str(db or catalog or "").strip()
        if schema and schema.lower() not in {item.lower() for item in config.allowed_schemas}:
            return "", "SQL_SCHEMA_NOT_ALLOWED"
    existing_limit = statement.args.get("limit")
    if existing_limit is None:
        statement = statement.limit(config.max_rows)
    elif isinstance(existing_limit, exp.Limit):
        expression = existing_limit.expression
        if isinstance(expression, exp.Literal) and expression.is_number and int(expression.this) > config.max_rows:
            statement = statement.limit(config.max_rows)
    return statement.sql(dialect=_dialect(config), pretty=False).strip(), None


def _json_value(value: Any, max_chars: int) -> Any:
    """把数据库值转换成有长度预算的 JSON 值。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def _bound_rows(
    rows: list[dict[str, Any]],
    columns: list[str],
    config: SqlServerConfig,
) -> dict[str, Any]:
    """限制 rows 数量、单元格长度和总返回字符数。"""
    values = [{str(key): _json_value(value, config.max_cell_chars) for key, value in row.items()} for row in rows[: config.max_rows]]
    bounded: list[dict[str, Any]] = []
    used_chars = 2
    for row in values:
        row_chars = len(json.dumps(row, ensure_ascii=False, default=str)) + 1
        if used_chars + row_chars > config.max_result_chars:
            break
        bounded.append(row)
        used_chars += row_chars
    return {
        "columns": columns,
        "rows": bounded,
        "row_count": len(rows),
        "returned_row_count": len(bounded),
        "truncated": len(bounded) < len(rows),
        "empty": not values,
    }


def _result_content(
    sql: str,
    *,
    columns: list[str],
    rows: list[dict[str, Any]],
    row_count: int,
    returned_row_count: int,
    truncated: bool,
) -> str:
    """生成传回 Lead-Agent 的可读 SQL 结果摘要。

    Args:
        sql: 实际执行的 SQL。
        columns: 查询列名。
        rows: 经过预算限制后保留的真实查询行。
        row_count: 服务端读取到的行数。
        returned_row_count: 实际返回给模型的行数。
        truncated: 是否因预算限制截断。

    Returns:
        包含 SQL、列和真实 rows 的文本内容。
    """
    result = {
        "columns": columns,
        "rows": rows,
        "row_count": row_count,
        "returned_row_count": returned_row_count,
        "truncated": truncated,
    }
    return f"当前执行SQL为:\n{sql}\nSQL结果为:\n{json.dumps(result, ensure_ascii=False, default=str)}"


def _error_result(
    error_code: str,
    *,
    sql: str | None = None,
    error_message: str | None = None,
    duration_ms: float | None = None,
) -> dict[str, Any]:
    """构造统一的 SQL MCP 错误结果。"""
    result: dict[str, Any] = {
        "ok": False,
        "error_code": error_code,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "returned_row_count": 0,
        "truncated": False,
        "empty": True,
    }
    if sql is not None:
        result["sql"] = sql
    if error_message:
        result["error_message"] = error_message
    if duration_ms is not None:
        result["duration_ms"] = duration_ms
    if sql:
        result["content"] = f"当前执行SQL为:\n{sql}\nSQL执行失败，错误代码: {error_code}"
    else:
        result["content"] = f"SQL执行失败，错误代码: {error_code}"
    return result


def _mysql_connection_kwargs(dsn: str, config: SqlServerConfig) -> dict[str, Any]:
    """把 mysql:// DSN 转换成 PyMySQL 连接参数。"""
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ValueError("MySQL DSN 必须使用 mysql:// 或 mysql+pymysql://")
    database = parsed.path.removeprefix("/")
    if not parsed.hostname or not parsed.username or not database:
        raise ValueError("MySQL DSN 缺少 host、user 或 database")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username),
        "password": unquote(parsed.password or ""),
        "database": database,
        "charset": "utf8mb4",
        "autocommit": False,
        "read_timeout": config.statement_timeout_seconds,
        "write_timeout": config.statement_timeout_seconds,
        "connect_timeout": config.statement_timeout_seconds,
    }


def execute_sql(sql: str, config: SqlServerConfig) -> dict[str, Any]:
    """在 MCP Server 内执行只读 SQL 并返回真实查询行。"""
    executable_sql, error_code = normalize_sql(sql, config)
    if error_code is not None:
        return _error_result(error_code, sql=sql.strip() if isinstance(sql, str) else None)
    dsn = os.getenv(config.dsn_env)
    if not dsn:
        return _error_result("SQL_DSN_MISSING", sql=executable_sql)
    started = time.perf_counter()
    connection = None
    try:
        if config.database_type == "mysql":
            import pymysql

            connection = pymysql.connect(**_mysql_connection_kwargs(dsn, config))
            with connection.cursor() as cursor:
                cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {config.statement_timeout_seconds * 1000}")
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(executable_sql)
                columns = [str(item[0]) for item in (cursor.description or [])]
                raw_rows = cursor.fetchmany(config.max_rows + 1)
                rows = [dict(zip(columns, row, strict=False)) for row in raw_rows]
                connection.rollback()
        else:
            import psycopg

            connection = psycopg.connect(dsn, connect_timeout=config.statement_timeout_seconds)
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
                cursor.execute(f"SET statement_timeout = {config.statement_timeout_seconds * 1000}")
                cursor.execute(executable_sql)
                columns = [str(item.name) for item in (cursor.description or [])]
                raw_rows = cursor.fetchmany(config.max_rows + 1)
                rows = [dict(zip(columns, row, strict=False)) for row in raw_rows]
                connection.rollback()
        bounded = _bound_rows(rows, columns, config)
        return {
            "ok": True,
            "sql": executable_sql,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            **bounded,
            "content": _result_content(
                executable_sql,
                columns=bounded["columns"],
                rows=bounded["rows"],
                row_count=bounded["row_count"],
                returned_row_count=bounded["returned_row_count"],
                truncated=bounded["truncated"],
            ),
        }
    except Exception as exc:
        return _error_result(
            "SQL_EXECUTION_FAILED",
            sql=executable_sql,
            error_message=str(exc)[:500],
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    finally:
        if connection is not None:
            connection.close()


def create_server(config: SqlServerConfig) -> FastMCP:
    """创建 SQL Execute MCP Server。"""
    mcp = FastMCP(
        config.name,
        host=config.host,
        port=config.port,
        streamable_http_path="/mcp",
    )

    @mcp.tool(
        name="sql_execute",
        description=("执行一条只读 SQL 查询。只需要传入 SQL 字符串。返回真实查询结果 rows、columns、row_count 和截断状态；不要把结果改写成只有行数或列数的摘要。"),
    )
    def sql_execute(sql: str) -> dict[str, Any]:
        """接收 SQL 字符串并返回真实查询数据。"""
        return execute_sql(sql, config)

    return mcp


def main() -> None:
    """解析参数并启动 MCP Server。"""
    parser = argparse.ArgumentParser(description="Run DeerFlow SQL Execute MCP Server.")
    parser.add_argument("--config", default=os.getenv("SQL_EXECUTE_CONFIG_PATH", str(Path(__file__).with_name("config.yaml"))))
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.transport or args.host or args.port:
        config = replace(
            config,
            transport=args.transport or config.transport,
            host=args.host or config.host,
            port=args.port or config.port,
        )
    create_server(config).run(transport=config.transport)


if __name__ == "__main__":
    main()
