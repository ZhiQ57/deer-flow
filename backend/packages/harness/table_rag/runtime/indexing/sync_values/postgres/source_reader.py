"""PostgreSQL 字段值源数据读取实现。"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from ....connections.base import ConnectionProvider
from ....connections.postgresql_cursor import execute_sql
from .....schemas import FieldValueFilter, FieldValueIndexTarget, TableValueIndexTarget
from ..base import ValueSourceReader

log = logging.getLogger(__name__)


def require_sql():
    """延迟导入 psycopg.sql。

    Args:
        无。

    Returns:
        psycopg.sql 模块。
    """
    try:
        from psycopg import sql
    except ImportError as exc:
        raise RuntimeError("psycopg is required. Install with: pip install 'psycopg[binary]>=3.1.18'") from exc
    return sql


class PostgresSourceValueReader(ValueSourceReader):
    """PostgreSQL 源表字段值读取器，用于抽取配置字段的 distinct 非空值。"""

    def __init__(self, connection_provider: ConnectionProvider):
        """初始化源表读取器。

        Args:
            connection_provider: 外部注入的业务源库连接提供器。

        Returns:
            None。
        """
        self.connection_provider = connection_provider

    def iter_distinct_values(self, target: TableValueIndexTarget, field: FieldValueIndexTarget) -> Iterable[str]:
        """从真实业务表读取一个字段的 distinct 非空值。

        Args:
            target: 当前逻辑表同步配置。
            field: 当前字段同步配置。

        Returns:
            字符串迭代器，逐个返回数据库真实字段值。
        """
        sql = require_sql()
        source_column = field.resolved_source_column
        limit_sql = sql.SQL("")
        if target.limit is not None:
            limit_sql = sql.SQL(" LIMIT {}").format(sql.Literal(target.limit))

        where_parts = [
            sql.SQL("{} IS NOT NULL").format(sql.Identifier(source_column)),
            sql.SQL("btrim({}::text) <> ''").format(sql.Identifier(source_column)),
        ]
        params: dict[str, object] = {}
        safe_filter_parts, safe_filter_params = _compile_filters(target.filters)
        where_parts.extend(safe_filter_parts)
        params.update(safe_filter_params)
        if target.unsafe_where_clause:
            # unsafe_where_clause 是显式高风险入口，只允许开发者/管理员配置，不能来自用户输入。
            where_parts.append(sql.SQL("({})").format(sql.SQL(target.unsafe_where_clause)))

        query = sql.SQL(
            """
            SELECT {column}::text AS raw_value
            FROM {schema}.{table}
            WHERE {where_clause}
            GROUP BY {column}
            ORDER BY {column}
            {limit_clause}
            """
        ).format(
            column=sql.Identifier(source_column),
            schema=sql.Identifier(target.source_schema),
            table=sql.Identifier(target.source_table),
            where_clause=sql.SQL(" AND ").join(where_parts),
            limit_clause=limit_sql,
        )

        with self.connection_provider.connect() as conn:
            with conn.cursor() as cur:
                log.info("Reading distinct values for %s.%s", target.table_name, field.column_name)
                execute_sql(cur, query, params or None)
                for row in cur:
                    raw_value = row[0]
                    if raw_value is not None and str(raw_value).strip():
                        yield str(raw_value)


def _compile_filters(filters: list[FieldValueFilter]) -> tuple[list[object], dict[str, object]]:
    """把字段值同步过滤 DSL 编译为参数化 PostgreSQL SQL 片段。

    Args:
        filters: 安全字段过滤条件列表。

    Returns:
        SQL 条件片段和参数字典。
    """
    sql = require_sql()
    parts: list[object] = []
    params: dict[str, object] = {}
    for index, filter_item in enumerate(filters):
        column = sql.Identifier(filter_item.column_name)
        param_name = f"filter_{index}"
        operator = filter_item.operator
        if operator == "is_null":
            parts.append(sql.SQL("{} IS NULL").format(column))
        elif operator == "is_not_null":
            parts.append(sql.SQL("{} IS NOT NULL").format(column))
        elif operator in {"in", "not_in"}:
            params[param_name] = filter_item.values
            sql_operator = " = ANY" if operator == "in" else " <> ALL"
            placeholder = sql.SQL("%({})s").format(sql.SQL(param_name))
            parts.append(sql.SQL("{}{}({})").format(column, sql.SQL(sql_operator), placeholder))
        elif operator in {"contains", "startswith", "endswith"}:
            params[param_name] = _like_pattern(operator, filter_item.value)
            placeholder = sql.SQL("%({})s").format(sql.SQL(param_name))
            parts.append(sql.SQL("{}::text ILIKE {}").format(column, placeholder))
        else:
            params[param_name] = filter_item.value
            placeholder = sql.SQL("%({})s").format(sql.SQL(param_name))
            parts.append(sql.SQL("{} {} {}").format(column, sql.SQL(_sql_operator(operator)), placeholder))
    return parts, params


def _sql_operator(operator: str) -> str:
    """把过滤 DSL 操作符转换成 SQL 操作符。

    Args:
        operator: 过滤 DSL 操作符。

    Returns:
        SQL 操作符。
    """
    return {
        "eq": "=",
        "ne": "<>",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
    }[operator]


def _like_pattern(operator: str, value: object) -> str:
    """生成 ILIKE 模糊匹配参数。

    Args:
        operator: contains / startswith / endswith。
        value: 原始匹配值。

    Returns:
        ILIKE 参数值。
    """
    text = str(value)
    if operator == "contains":
        return f"%{text}%"
    if operator == "startswith":
        return f"{text}%"
    return f"%{text}"
