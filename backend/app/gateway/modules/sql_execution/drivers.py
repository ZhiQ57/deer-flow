"""Gateway SQL Execution 数据库驱动适配器。"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig


def execute_postgres(
    sql: str,
    dsn: str,
    config: DataQueryServiceAbilityConfig,
) -> tuple[list[str], list[Any]]:
    """在 PostgreSQL 只读事务中执行 SQL。

    Args:
        sql: 已通过 Gateway 校验的规范 SQL。
        dsn: 当前调用栈内解析的数据库 DSN。
        config: 当前 DataAgent 查询能力配置。

    Returns:
        列名和原始数据库行。
    """
    import psycopg

    with psycopg.connect(
        dsn,
        connect_timeout=config.sql_execution.statement_timeout_seconds,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{config.sql_execution.statement_timeout_seconds * 1000}ms",),
            )
            cursor.execute(sql)
            columns = [str(item.name) for item in cursor.description or []]
            rows = list(cursor.fetchmany(config.sql_execution.max_rows + 1))
    return columns, rows


def execute_mysql(
    sql: str,
    dsn: str,
    config: DataQueryServiceAbilityConfig,
) -> tuple[list[str], list[Any]]:
    """在 MySQL 只读事务中执行 SQL。

    Args:
        sql: 已通过 Gateway 校验的规范 SQL。
        dsn: 当前调用栈内解析的数据库 DSN。
        config: 当前 DataAgent 查询能力配置。

    Returns:
        列名和原始数据库行。

    Raises:
        ValueError: DSN scheme 不是 MySQL。
    """
    import pymysql

    parsed = urlsplit(dsn)
    if parsed.scheme.lower() not in {"mysql", "mysql+pymysql"}:
        raise ValueError("SQL_DSN_SCHEME_INVALID")
    database = unquote(parsed.path.lstrip("/"))
    query = parse_qs(parsed.query)
    connection = pymysql.connect(
        host=parsed.hostname or "",
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=database,
        charset=(query.get("charset") or ["utf8mb4"])[0],
        connect_timeout=config.sql_execution.statement_timeout_seconds,
        read_timeout=config.sql_execution.statement_timeout_seconds,
        write_timeout=config.sql_execution.statement_timeout_seconds,
        autocommit=False,
        local_infile=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute(
                f"SET SESSION MAX_EXECUTION_TIME = {config.sql_execution.statement_timeout_seconds * 1000}",
            )
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute(sql)
            columns = [str(item[0]) for item in cursor.description or []]
            rows = list(cursor.fetchmany(config.sql_execution.max_rows + 1))
        return columns, rows
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()
