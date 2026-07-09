"""PostgreSQL 检索器公共工具。"""

from __future__ import annotations

from collections.abc import Iterable


from ...providers.embedding import normalize_embedding_vector
from ...runtime.connections.base import ConnectionProvider
from ...runtime.connections.postgresql_cursor import execute_sql
from ...utils.validation import validate_safe_identifier


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


def validate_pg_name(value: str, label: str) -> None:
    """校验 PostgreSQL 标识符是否安全。

    Args:
        value: 待校验标识符。
        label: 错误信息中的字段名。

    Returns:
        None。
    """
    validate_safe_identifier(value, label)


def format_pgvector(values: Iterable[float], expected_dimension: int | None = None) -> str:
    """校验外部向量并转换成 pgvector 文本格式。

    Args:
        values: 查询向量，必须为非空、有限数值、非全 0 序列。

    Returns:
        pgvector 可识别的字符串，例如 [0.1,0.2]。
    """
    normalized = normalize_embedding_vector(
        values,
        expected_dimension=expected_dimension,
        label="query vector",
    )
    return "[" + ",".join(f"{value:.8g}" for value in normalized) + "]"


def retrieval_candidate_limit(top_k: int, *, multiplier: int = 20, minimum: int = 50) -> int:
    """计算单路召回候选数量，给外层融合保留足够候选。

    Args:
        top_k: 最终返回数量。
        multiplier: 单路候选扩展倍数。
        minimum: 单路候选数量下限。

    Returns:
        单路候选数量。
    """
    return max(top_k * multiplier, minimum)


def jsonb_list(value) -> list[str]:
    """把 PostgreSQL JSONB 返回值安全转换为字符串列表。

    Args:
        value: psycopg 返回的 JSONB 值。

    Returns:
        字符串列表。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]
