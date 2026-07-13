"""DataAgent MySQL 只读 SQL 校验能力。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_DANGEROUS_FUNCTIONS = frozenset(
    {
        "BENCHMARK",
        "GET_LOCK",
        "IS_FREE_LOCK",
        "IS_USED_LOCK",
        "LOAD_FILE",
        "MASTER_POS_WAIT",
        "RELEASE_ALL_LOCKS",
        "RELEASE_LOCK",
        "SLEEP",
        "SOURCE_POS_WAIT",
        "SYS_EVAL",
        "SYS_EXEC",
    }
)
_FILE_OUTPUT_PATTERN = re.compile(r"\bINTO\s+(?:OUTFILE|DUMPFILE)\b", flags=re.IGNORECASE)
_MYSQL_EXECUTABLE_COMMENT_PATTERN = re.compile(r"/\*!\s*\d{0,6}\s*", flags=re.IGNORECASE)
_MAX_SQL_CHARS = 100_000
_MYSQL_SYSTEM_DATABASES = frozenset({"information_schema", "mysql", "performance_schema", "sys"})

_FORBIDDEN_NODE_TYPES = (
    exp.Alter,
    exp.Analyze,
    exp.Attach,
    exp.Cache,
    exp.Command,
    exp.Copy,
    exp.Create,
    exp.Delete,
    exp.Detach,
    exp.Describe,
    exp.Drop,
    exp.Execute,
    exp.Grant,
    exp.Hint,
    exp.Insert,
    exp.Into,
    exp.Kill,
    exp.LoadData,
    exp.Lock,
    exp.Merge,
    exp.Pragma,
    exp.Revoke,
    exp.Set,
    exp.Show,
    exp.Transaction,
    exp.TruncateTable,
    exp.Uncache,
    exp.Update,
    exp.Use,
)


class SQLValidationError(ValueError):
    """DataAgent SQL 安全校验失败。"""


@dataclass(frozen=True)
class SQLValidationResult:
    """只读 SQL 校验结果。"""

    valid: bool
    original_sql: str
    executable_sql: str
    source_sql_sha256: str
    sql_sha256: str
    max_rows: int
    effective_limit: int
    limit_applied: bool
    tables: list[str]
    columns: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。

        Args:
            无。

        Return:
            SQL 校验结果字典。
        """
        return asdict(self)


def sql_sha256(sql: str) -> str:
    """计算 SQL 文本摘要。

    Args:
        sql: SQL 文本。

    Return:
        SHA-256 十六进制摘要。
    """
    return hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()


def _extract_literal_limit(expression: exp.Query) -> int | None:
    """读取查询根节点的字面量 LIMIT。

    Args:
        expression: sqlglot 查询表达式。

    Return:
        LIMIT 整数；未配置返回 None。
    """
    limit = expression.args.get("limit")
    if limit is None:
        return None
    value = limit.args.get("expression")
    if not isinstance(value, exp.Literal) or not value.is_int:
        raise SQLValidationError("LIMIT 必须是固定正整数，不能使用变量、表达式或占位符。")
    parsed = int(value.this)
    if parsed <= 0:
        raise SQLValidationError("LIMIT 必须大于 0。")
    return parsed


def _validate_ast(expression: exp.Expression) -> None:
    """校验 SQL AST 仅包含只读查询节点。

    Args:
        expression: sqlglot 根表达式。

    Return:
        None。
    """
    if not isinstance(expression, exp.Query):
        raise SQLValidationError(f"仅允许 SELECT/WITH 查询，当前语句类型为 {type(expression).__name__}。")

    for node in expression.walk():
        if isinstance(node, _FORBIDDEN_NODE_TYPES):
            raise SQLValidationError(f"SQL 包含禁止的节点：{type(node).__name__}。")
        if isinstance(node, (exp.Parameter, exp.Placeholder)):
            raise SQLValidationError("SQL 包含未绑定变量或占位符，请先替换为明确的只读查询条件。")
        if isinstance(node, exp.PropertyEQ):
            raise SQLValidationError("SQL 不允许给会话变量赋值。")
        if isinstance(node, exp.Anonymous) and node.name.upper() in _DANGEROUS_FUNCTIONS:
            raise SQLValidationError(f"SQL 不允许调用危险函数 {node.name.upper()}。")


def _validate_database_scope(expression: exp.Expression, allowed_database: str | None) -> None:
    """限制 SQL 只能访问配置的业务数据库。

    Args:
        expression: sqlglot 根表达式。
        allowed_database: 允许访问的 MySQL 数据库名；为空时仍禁止系统库。

    Return:
        None。
    """
    normalized_allowed = allowed_database.strip().casefold() if allowed_database else None
    for table in expression.find_all(exp.Table):
        database = (table.db or table.catalog or "").strip()
        if not database:
            continue
        normalized_database = database.casefold()
        if normalized_database in _MYSQL_SYSTEM_DATABASES:
            raise SQLValidationError(f"SQL 不允许访问 MySQL 系统数据库 {database}。")
        if normalized_allowed is not None and normalized_database != normalized_allowed:
            raise SQLValidationError(f"SQL 只允许访问数据库 {allowed_database}，禁止跨库访问 {database}。")


def _extract_tables(expression: exp.Expression) -> list[str]:
    """抽取查询引用的真实表名。

    Args:
        expression: sqlglot 根表达式。

    Return:
        去重后的表名列表。
    """
    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name}
    tables: list[str] = []
    for table in expression.find_all(exp.Table):
        name = table.name
        if not name or name.lower() in cte_names:
            continue
        qualified = ".".join(part for part in (table.catalog, table.db, name) if part)
        if qualified not in tables:
            tables.append(qualified)
    return tables


def _extract_columns(expression: exp.Expression) -> list[str]:
    """抽取查询引用的列名。

    Args:
        expression: sqlglot 根表达式。

    Return:
        去重后的列名列表。
    """
    columns: list[str] = []
    for column in expression.find_all(exp.Column):
        name = ".".join(part for part in (column.table, column.name) if part)
        if name and name not in columns:
            columns.append(name)
    return columns


def validate_readonly_sql(
    sql: str,
    *,
    max_rows: int = 200,
    allowed_database: str | None = None,
) -> SQLValidationResult:
    """按 MySQL 方言校验并收紧只读 SQL。

    Args:
        sql: 待校验 SQL。
        max_rows: 最大返回行数。
        allowed_database: 可选业务数据库名；配置后禁止跨库访问。

    Return:
        可直接交给执行层的校验结果。

    Raises:
        SQLValidationError: SQL 为空、不合法、包含多语句或越过只读边界。
    """
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows <= 0:
        raise ValueError("max_rows 必须是正整数。")
    source_sql = sql.strip()
    if not source_sql:
        raise SQLValidationError("SQL 不能为空。")
    if len(source_sql) > _MAX_SQL_CHARS:
        raise SQLValidationError(f"SQL 长度不能超过 {_MAX_SQL_CHARS} 个字符。")
    if _FILE_OUTPUT_PATTERN.search(source_sql):
        raise SQLValidationError("SQL 不允许使用 INTO OUTFILE/DUMPFILE 写文件。")
    if _MYSQL_EXECUTABLE_COMMENT_PATTERN.search(source_sql):
        raise SQLValidationError("SQL 不允许使用 MySQL 可执行注释。")

    try:
        statements = [statement for statement in sqlglot.parse(source_sql, read="mysql") if statement is not None]
    except ParseError as exc:
        raise SQLValidationError(f"MySQL SQL 解析失败：{exc}") from exc

    if len(statements) != 1:
        raise SQLValidationError("一次只允许执行一条 SQL 语句。")

    expression = statements[0]
    _validate_ast(expression)
    _validate_database_scope(expression, allowed_database)
    if not isinstance(expression, exp.Query):
        raise SQLValidationError("仅允许 SELECT/WITH 查询。")

    existing_limit = _extract_literal_limit(expression)
    limit_applied = existing_limit is None or existing_limit > max_rows
    if limit_applied:
        expression = expression.copy().limit(max_rows)
    effective_limit = min(existing_limit, max_rows) if existing_limit is not None else max_rows

    executable_sql = expression.sql(dialect="mysql", pretty=False)
    return SQLValidationResult(
        valid=True,
        original_sql=source_sql,
        executable_sql=executable_sql,
        source_sql_sha256=sql_sha256(source_sql),
        sql_sha256=sql_sha256(executable_sql),
        max_rows=max_rows,
        effective_limit=effective_limit,
        limit_applied=limit_applied,
        tables=_extract_tables(expression),
        columns=_extract_columns(expression),
        warnings=[],
    )
