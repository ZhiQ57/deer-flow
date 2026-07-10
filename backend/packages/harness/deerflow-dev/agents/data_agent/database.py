"""DataAgent MySQL 只读执行层。"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .sql_validation import validate_readonly_sql

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ROWS = 200
_DEFAULT_QUERY_TIMEOUT_MS = 30_000
_DEFAULT_CONNECT_TIMEOUT = 10
_DEFAULT_READ_TIMEOUT = 30
_DEFAULT_WRITE_TIMEOUT = 30
_DEFAULT_MAX_CELL_CHARS = 2_000
_DEFAULT_MAX_RESULT_CHARS = 200_000
_DSN_CREDENTIAL_PATTERN = re.compile(
    r"(?P<prefix>[a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@\s/]+(?=@)",
    flags=re.IGNORECASE,
)
_PASSWORD_FIELD_PATTERN = re.compile(
    r"(?P<prefix>\b(?:password|passwd|pwd)\s*[=:]\s*)(?P<quote>['\"]?)[^,;\s'\"}]+(?P=quote)",
    flags=re.IGNORECASE,
)


def _read_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    """读取有范围约束的整数环境变量。

    Args:
        env: 环境变量映射。
        name: 变量名。
        default: 默认值。
        minimum: 最小值。
        maximum: 可选最大值。

    Return:
        校验后的整数。
    """
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数。") from exc
    if value < minimum or (maximum is not None and value > maximum):
        upper = f"，且不大于 {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} 必须不小于 {minimum}{upper}。")
    return value


def resolve_sql_max_rows(environ: Mapping[str, str] | None = None) -> int:
    """读取 DataAgent SQL 最大返回行数。

    Args:
        environ: 可选环境变量映射。

    Return:
        最大返回行数。
    """
    return _read_int(environ or os.environ, "DATA_AGENT_SQL_MAX_ROWS", _DEFAULT_MAX_ROWS, maximum=10_000)


@dataclass(frozen=True)
class MySQLExecutionSettings:
    """DataAgent MySQL 连接和结果预算配置。"""

    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"
    connect_timeout: int = _DEFAULT_CONNECT_TIMEOUT
    read_timeout: int = _DEFAULT_READ_TIMEOUT
    write_timeout: int = _DEFAULT_WRITE_TIMEOUT
    query_timeout_ms: int = _DEFAULT_QUERY_TIMEOUT_MS
    max_rows: int = _DEFAULT_MAX_ROWS
    max_cell_chars: int = _DEFAULT_MAX_CELL_CHARS
    max_result_chars: int = _DEFAULT_MAX_RESULT_CHARS

    def __post_init__(self) -> None:
        """校验连接和预算配置。"""
        if not self.host.strip():
            raise ValueError("MySQL host 不能为空。")
        if not 0 < self.port <= 65535:
            raise ValueError("MySQL port 必须在 1 到 65535 之间。")
        if not self.user.strip():
            raise ValueError("MySQL user 不能为空。")
        if not self.database.strip():
            raise ValueError("MySQL database 不能为空。")
        for name in (
            "connect_timeout",
            "read_timeout",
            "write_timeout",
            "query_timeout_ms",
            "max_rows",
            "max_cell_chars",
            "max_result_chars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数。")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MySQLExecutionSettings:
        """从环境变量读取 MySQL 执行配置。

        Args:
            environ: 可选环境变量映射。

        Return:
            MySQL 执行配置。
        """
        env = environ or os.environ
        dsn = (env.get("DATA_AGENT_MYSQL_DSN") or "").strip()
        query: dict[str, list[str]] = {}
        if dsn:
            parsed = urlparse(dsn)
            if parsed.scheme not in {"mysql", "mysql+pymysql"}:
                raise ValueError("DATA_AGENT_MYSQL_DSN 仅支持 mysql 或 mysql+pymysql scheme。")
            host = parsed.hostname or ""
            port = parsed.port or 3306
            user = unquote(parsed.username or "")
            password = unquote(parsed.password or "")
            database = unquote(parsed.path.lstrip("/"))
            query = parse_qs(parsed.query)
        else:
            host = (env.get("DATA_AGENT_MYSQL_HOST") or "").strip()
            port = _read_int(env, "DATA_AGENT_MYSQL_PORT", 3306, maximum=65535)
            user = (env.get("DATA_AGENT_MYSQL_USER") or "").strip()
            password = env.get("DATA_AGENT_MYSQL_PASSWORD") or ""
            database = (env.get("DATA_AGENT_MYSQL_DATABASE") or "").strip()

        charset = (env.get("DATA_AGENT_MYSQL_CHARSET") or (query.get("charset") or ["utf8mb4"])[0]).strip()
        return cls(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset=charset or "utf8mb4",
            connect_timeout=_read_int(env, "DATA_AGENT_MYSQL_CONNECT_TIMEOUT", _DEFAULT_CONNECT_TIMEOUT, maximum=300),
            read_timeout=_read_int(env, "DATA_AGENT_MYSQL_READ_TIMEOUT", _DEFAULT_READ_TIMEOUT, maximum=3600),
            write_timeout=_read_int(env, "DATA_AGENT_MYSQL_WRITE_TIMEOUT", _DEFAULT_WRITE_TIMEOUT, maximum=3600),
            query_timeout_ms=_read_int(env, "DATA_AGENT_SQL_TIMEOUT_MS", _DEFAULT_QUERY_TIMEOUT_MS, maximum=3_600_000),
            max_rows=resolve_sql_max_rows(env),
            max_cell_chars=_read_int(env, "DATA_AGENT_SQL_MAX_CELL_CHARS", _DEFAULT_MAX_CELL_CHARS, maximum=100_000),
            max_result_chars=_read_int(env, "DATA_AGENT_SQL_MAX_RESULT_CHARS", _DEFAULT_MAX_RESULT_CHARS, maximum=10_000_000),
        )

    def safe_description(self) -> str:
        """返回不包含明文密码的连接描述。

        Args:
            无。

        Return:
            脱敏连接描述。
        """
        return f"mysql+pymysql://{self.user}:***@{self.host}:{self.port}/{self.database}"

    def connect_kwargs(self) -> dict[str, Any]:
        """构造 PyMySQL 连接参数。

        Args:
            无。

        Return:
            PyMySQL 关键字参数。
        """
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "charset": self.charset,
            "connect_timeout": self.connect_timeout,
            "read_timeout": self.read_timeout,
            "write_timeout": self.write_timeout,
            "autocommit": False,
            "local_infile": False,
        }


def format_safe_database_error(
    exc: Exception,
    *,
    settings: MySQLExecutionSettings | None = None,
    max_chars: int = 500,
) -> str:
    """构造不会泄露数据库凭据的错误摘要。

    Args:
        exc: 数据库或驱动异常。
        settings: 可选当前 MySQL 连接配置；配置解析失败时可为空。
        max_chars: 最大错误文本字符数。

    Return:
        包含异常类型、但已脱敏并截断的错误摘要。
    """
    if max_chars <= 0:
        raise ValueError("max_chars 必须是正整数。")
    message = str(exc)
    password = settings.password if settings is not None else ""
    secrets = {
        password,
        quote(password, safe="") if password else "",
    }
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    message = _DSN_CREDENTIAL_PATTERN.sub(r"\g<prefix>***", message)
    message = _PASSWORD_FIELD_PATTERN.sub(r"\g<prefix>***", message)
    normalized = message.strip() or "数据库操作失败。"
    return f"{exc.__class__.__name__}: {_truncate_text(normalized, max_chars)}"


def _default_connect_factory(**kwargs: Any):
    """创建默认 PyMySQL 连接。

    Args:
        kwargs: PyMySQL 连接参数。

    Return:
        PyMySQL Connection。
    """
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("DataAgent MySQL 执行需要安装 deerflow-harness[data-agent]。") from exc
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **kwargs)


def _truncate_text(value: str, max_chars: int) -> str:
    """按字符数截断单元格文本。

    Args:
        value: 原始文本。
        max_chars: 最大字符数。

    Return:
        截断后的文本。
    """
    if len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return value[:max_chars]
    return value[: max_chars - 3] + "..."


def _to_json_value(value: Any, *, max_chars: int) -> Any:
    """把数据库值转换为受预算保护的 JSON 值。

    Args:
        value: 数据库返回值。
        max_chars: 单元格最大字符数。

    Return:
        可 JSON 序列化的值。
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return _truncate_text(value.decode("utf-8", errors="replace"), max_chars)
    if isinstance(value, str):
        return _truncate_text(value, max_chars)
    return _truncate_text(str(value), max_chars)


def execute_readonly_sql(
    sql: str,
    *,
    settings: MySQLExecutionSettings | None = None,
    connect_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """校验并执行单条 MySQL 只读 SQL。

    Args:
        sql: 待执行 SQL。
        settings: 可选执行配置；为空时从环境变量读取。
        connect_factory: 可选连接工厂，测试可注入假连接。

    Return:
        包含列、行、截断状态和耗时的执行结果。
    """
    resolved = settings or MySQLExecutionSettings.from_env()
    validation = validate_readonly_sql(
        sql,
        max_rows=resolved.max_rows,
        allowed_database=resolved.database,
    )
    connector = connect_factory or _default_connect_factory
    connection = None
    started = time.perf_counter()

    try:
        connection = connector(**resolved.connect_kwargs())
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {resolved.query_timeout_ms}")
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute(validation.executable_sql)
            raw_rows = list(cursor.fetchmany(resolved.max_rows + 1))
            columns = [str(item[0]) for item in (cursor.description or [])]

        row_limit_truncated = len(raw_rows) > resolved.max_rows or (validation.effective_limit >= resolved.max_rows and len(raw_rows) >= resolved.max_rows)
        rows: list[dict[str, Any]] = []
        result_chars = 0
        result_budget_truncated = False
        for raw_row in raw_rows[: resolved.max_rows]:
            if isinstance(raw_row, Mapping):
                row = {str(key): _to_json_value(value, max_chars=resolved.max_cell_chars) for key, value in raw_row.items()}
            else:
                row = {columns[index] if index < len(columns) else f"column_{index + 1}": _to_json_value(value, max_chars=resolved.max_cell_chars) for index, value in enumerate(raw_row)}
            row_chars = len(json.dumps(row, ensure_ascii=False, default=str))
            if result_chars + row_chars > resolved.max_result_chars:
                result_budget_truncated = True
                break
            rows.append(row)
            result_chars += row_chars

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": True,
            "sql_sha256": validation.sql_sha256,
            "executable_sql": validation.executable_sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": row_limit_truncated or result_budget_truncated,
            "elapsed_ms": elapsed_ms,
        }
    finally:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                logger.warning("DataAgent MySQL rollback failed.", exc_info=True)
            try:
                connection.close()
            except Exception:
                logger.warning("DataAgent MySQL connection close failed.", exc_info=True)
