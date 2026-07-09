"""PostgreSQL cursor 执行兼容工具。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def render_sql(statement: Any, context: Any | None = None) -> Any:
    """把 psycopg SQL 组合对象渲染为 DB-API cursor 可执行对象。

    Args:
        statement: SQL 字符串或 psycopg.sql.Composable 对象。
        context: 可选 cursor / connection，用于 psycopg2 风格 as_string 渲染。

    Returns:
        普通 SQL 字符串或原始 statement。
    """
    as_string = getattr(statement, "as_string", None)
    if not callable(as_string):
        return statement
    try:
        return as_string()
    except TypeError:
        if context is None:
            raise
        return as_string(context)


def execute_sql(cursor: Any, statement: Any, params: Any | None = None) -> Any:
    """执行 SQL，兼容只接受字符串 SQL 的 cursor。

    Args:
        cursor: DB-API cursor。
        statement: SQL 字符串或 psycopg.sql.Composable 对象。
        params: 可选 SQL 参数。

    Returns:
        cursor.execute 的返回值。
    """
    rendered = render_sql(statement, cursor)
    if params is None:
        return cursor.execute(rendered)
    return cursor.execute(rendered, params)


def executemany_sql(cursor: Any, statement: Any, rows: Sequence[Any]) -> Any:
    """批量执行 SQL，兼容只接受字符串 SQL 的 cursor。

    Args:
        cursor: DB-API cursor。
        statement: SQL 字符串或 psycopg.sql.Composable 对象。
        rows: 参数行集合。

    Returns:
        cursor.executemany 的返回值。
    """
    return cursor.executemany(render_sql(statement, cursor), rows)
