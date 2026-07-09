"""PostgreSQL 字段值索引存储实现。"""

from __future__ import annotations

from collections.abc import Sequence

from ..sync_records import record_search_text
from .....configs import IndexStoreSettings
from ....connections.base import ConnectionProvider
from ....connections.postgresql_cursor import execute_sql, executemany_sql
from .....schemas import FieldValueRecord
from ..base import ValueIndexStore
from .....utils.serialization import json_dumps
from .....utils.validation import validate_safe_identifier


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


def validate_pg_name(value: str, label: str) -> None:
    """校验 PostgreSQL 标识符是否安全。

    Args:
        value: 待校验标识符。
        label: 错误信息中的字段名。

    Returns:
        None。
    """
    validate_safe_identifier(value, label)


def qualified_table(schema_name: str, table_name: str):
    """生成安全的 schema.table SQL 标识符。

    Args:
        schema_name: PostgreSQL schema 名。
        table_name: PostgreSQL 表名。

    Returns:
        psycopg.sql.SQL 对象。
    """
    validate_pg_name(schema_name, "schema_name")
    validate_pg_name(table_name, "table_name")
    sql = require_sql()
    return sql.SQL("{}.{}").format(sql.Identifier(schema_name), sql.Identifier(table_name))


class PostgresValueIndexStore(ValueIndexStore):
    """PostgreSQL 字段值索引存储，实现索引数据写入和删除。"""

    def __init__(self, connection_provider: ConnectionProvider, settings: IndexStoreSettings):
        """初始化 PostgreSQL 索引存储。

        Args:
            connection_provider: 外部注入的索引库连接提供器。
            settings: 索引表和检索参数配置。

        Returns:
            None。
        """
        validate_pg_name(settings.schema_name, "schema_name")
        validate_pg_name(settings.field_value_table_name, "table_name")
        self.connection_provider = connection_provider
        self.settings = settings

    def upsert_values(self, records: Sequence[FieldValueRecord]) -> int:
        """批量写入字段值索引记录。

        Args:
            records: 已构造好的字段值索引记录。

        Returns:
            本次提交到数据库的记录数量。
        """
        if not records:
            return 0
        sql = require_sql()
        rows = [
            (
                record.raw_value,
                record.table_name,
                record.column_name,
                json_dumps(record.aliases),
                record_search_text(record),
            )
            for record in records
        ]
        statement = sql.SQL(
            """
            INSERT INTO {table} (
                raw_value, table_name, column_name, aliases, retrieval_text
            )
            VALUES (
                %s, %s, %s, %s::jsonb, %s
            )
            ON CONFLICT (table_name, column_name, raw_value)
            DO UPDATE SET
                aliases = EXCLUDED.aliases,
                retrieval_text = EXCLUDED.retrieval_text,
                updated_at = now()
            """
        ).format(table=self._table())

        with self.connection_provider.connect() as conn:
            with conn.cursor() as cur:
                executemany_sql(cur, statement, rows)
            conn.commit()
        return len(rows)

    def delete_target(self, table_name: str, column_name: str | None = None) -> int:
        """删除指定逻辑表或字段的索引记录。

        Args:
            table_name: 逻辑表名。
            column_name: 可选字段名；为空时删除整张逻辑表的索引记录。

        Returns:
            被删除的记录数量。
        """
        sql = require_sql()
        if column_name:
            statement = sql.SQL("DELETE FROM {table} WHERE table_name = %s AND column_name = %s").format(
                table=self._table()
            )
            params = (table_name, column_name)
        else:
            statement = sql.SQL("DELETE FROM {table} WHERE table_name = %s").format(table=self._table())
            params = (table_name,)

        with self.connection_provider.connect() as conn:
            with conn.cursor() as cur:
                execute_sql(cur, statement, params)
                count = cur.rowcount
            conn.commit()
        return count

    def _table(self):
        """生成带 schema 的安全索引表名。

        Args:
            无。

        Returns:
            psycopg.sql.SQL 对象，表示已正确转义的 schema.table。
        """
        return qualified_table(self.settings.schema_name, self.settings.field_value_table_name)
