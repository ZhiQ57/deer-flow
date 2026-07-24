"""DataAgent PostgreSQL/MySQL 只读 SQL 校验与执行服务。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from hashlib import sha256
from typing import Any, Literal, TypedDict
from urllib.parse import parse_qs, unquote, urlsplit

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .binding import resolve_data_source_binding, resolve_execution_dsn
from .config import DataQueryServiceAbilityConfig

SqlExecutionSource = Literal["subagent", "manual_ui"]

_DANGEROUS_FUNCTIONS = frozenset(
    {
        "benchmark",
        "dblink",
        "dblink_connect",
        "dblink_exec",
        "current_database",
        "current_schema",
        "current_setting",
        "current_user",
        "database",
        "get_lock",
        "is_free_lock",
        "is_used_lock",
        "load_file",
        "lo_export",
        "lo_import",
        "master_pos_wait",
        "nextval",
        "pg_advisory_lock",
        "pg_cancel_backend",
        "pg_logical_emit_message",
        "pg_ls_dir",
        "pg_read_binary_file",
        "pg_read_file",
        "pg_sleep",
        "pg_stat_file",
        "pg_terminate_backend",
        "release_all_locks",
        "release_lock",
        "set_config",
        "setval",
        "session_user",
        "sleep",
        "source_pos_wait",
        "system_user",
        "sys_eval",
        "sys_exec",
        "user",
        "version",
    }
)
_MYSQL_SYSTEM_DATABASES = frozenset({"information_schema", "mysql", "performance_schema", "sys"})
_MYSQL_EXECUTABLE_COMMENT_PATTERN = re.compile(r"/\*!\s*\d{0,6}\s*", flags=re.IGNORECASE)
_URI_CREDENTIAL_PATTERN = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^@\s/]+)@", flags=re.IGNORECASE)
_FORBIDDEN_SQL_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Grant,
    exp.Revoke,
    exp.Use,
    exp.Set,
    exp.Commit,
    exp.Rollback,
    exp.Command,
    exp.Transaction,
)


class SqlValidationResult(TypedDict, total=False):
    """SQL 校验结果合同。"""

    version: int
    valid: bool
    error_code: str
    executable_sql: str
    sql_sha256: str
    validation_digest: str
    snapshot_id: str | None
    database_type: str
    binding_fingerprint: str
    row_limit_applied: bool
    source: SqlExecutionSource


class SqlExecutionResult(TypedDict, total=False):
    """SQL 执行结果合同。"""

    version: int
    ok: bool
    database_type: str
    error_code: str
    error_category: str
    error_message: str
    retryable: bool
    recommended_action: str
    duration_ms: float
    sql_sha256: str | None
    validation_digest: str | None
    snapshot_id: str
    attempt: int
    max_attempts: int
    row_count: int
    returned_row_count: int
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    empty: bool


@dataclass(frozen=True, slots=True)
class SqlValidationRequest:
    """SQL 校验请求。

    Attributes:
        sql: SQL 模型生成的候选 SQL。
        retrieval: 当前 Query Snapshot 的 TableRAG registry 和数据源绑定；前端手动执行不需要提供。
        snapshot_id: 当前已批准 Query Snapshot 标识。
        source: 调用来源；SQL SubAgent 或前端手动执行。
    """

    sql: str
    retrieval: Mapping[str, Any] | None = None
    snapshot_id: str | None = None
    source: SqlExecutionSource = "subagent"


@dataclass(frozen=True, slots=True)
class SqlExecutionRequest:
    """SQL 执行请求。

    Attributes:
        sql: ``data_validate_sql`` 返回的规范 SQL。
        validation_digest: 校验结果绑定的服务端摘要。
        validation: 最近一次 SQL 校验结果。
        source: 调用来源；必须与校验来源一致。
    """

    sql: str
    validation_digest: str
    validation: Mapping[str, Any]
    source: SqlExecutionSource = "subagent"


@dataclass(frozen=True, slots=True)
class _ExecutionError:
    """数据库异常的安全结构化分类。"""

    error_code: str
    category: str
    retryable: bool
    recommended_action: str
    message: str | None = None


def sql_digest(sql: str) -> str:
    """计算规范化 SQL 的 SHA-256 摘要。

    Args:
        sql: 需要计算摘要的 SQL 或绑定文本。

    Returns:
        带 ``sha256:`` 前缀的摘要。
    """
    return f"sha256:{sha256(sql.encode('utf-8')).hexdigest()}"


def _allowed_object(name: str, allowlist: list[str]) -> bool:
    """按大小写不敏感方式匹配数据库对象白名单。

    Args:
        name: Schema、Table 或 Column 名称。
        allowlist: 服务端配置的授权对象列表。

    Returns:
        名称是否位于白名单。
    """
    if not allowlist:
        return False
    lowered = name.lower()
    return any(item.lower() == lowered for item in allowlist)


def _dialect(config: DataQueryServiceAbilityConfig) -> str:
    """返回 sqlglot 使用的方言名称。

    Args:
        config: 当前 DataAgent 查询能力配置。

    Returns:
        ``mysql`` 或 ``postgres``。
    """
    return "mysql" if config.sql_execution.database_type == "mysql" else "postgres"


def _default_schema(config: DataQueryServiceAbilityConfig) -> str:
    """返回未限定表名使用的默认 Schema。

    Args:
        config: 当前 DataAgent 查询能力配置。

    Returns:
        MySQL 允许列表中的首个 Database，或 PostgreSQL ``public``。
    """
    if config.sql_execution.database_type == "mysql":
        return config.sql_execution.allowed_schemas[0]
    return "public"


def _validate_sql(
    request: SqlValidationRequest,
    *,
    config: DataQueryServiceAbilityConfig,
    manual_binding: Mapping[str, Any] | None = None,
) -> SqlValidationResult:
    """执行只读 SQL AST、Evidence 和数据源绑定校验。

    Args:
        request: SQL 校验请求。
        config: 当前 DataAgent 查询能力配置。
        manual_binding: 服务端为前端手动执行解析的数据源绑定。

    Returns:
        版本化 SQL 校验结果。
    """
    sql = request.sql
    retrieval = request.retrieval
    if request.source not in {"subagent", "manual_ui"}:
        return {"version": 1, "valid": False, "error_code": "SQL_SOURCE_INVALID"}
    if not isinstance(sql, str) or not sql.strip():
        return {"version": 1, "valid": False, "error_code": "SQL_EMPTY"}
    if config.sql_execution.database_type == "mysql" and _MYSQL_EXECUTABLE_COMMENT_PATTERN.search(sql):
        return {"version": 1, "valid": False, "error_code": "SQL_EXECUTABLE_COMMENT_FORBIDDEN"}
    if not config.sql_execution.allowed_schemas or not config.sql_execution.allowed_tables or not config.sql_execution.allowed_columns:
        return {"version": 1, "valid": False, "error_code": "SQL_ALLOWLIST_REQUIRED"}
    binding = manual_binding if request.source == "manual_ui" else retrieval.get("binding") if isinstance(retrieval, Mapping) else None
    if not isinstance(binding, Mapping) or not isinstance(binding.get("binding_fingerprint"), str):
        return {"version": 1, "valid": False, "error_code": "SQL_BINDING_REQUIRED"}
    if binding.get("data_source_id") != config.data_source_id or binding.get("database_type") != config.sql_execution.database_type:
        return {"version": 1, "valid": False, "error_code": "SQL_BINDING_MISMATCH"}
    try:
        statements = [item for item in parse(sql, read=_dialect(config)) if item is not None]
    except ParseError:
        return {"version": 1, "valid": False, "error_code": "SQL_PARSE_ERROR"}
    if len(statements) != 1:
        return {"version": 1, "valid": False, "error_code": "SQL_MULTIPLE_STATEMENTS"}
    parsed = statements[0]
    if not isinstance(parsed, exp.Expression):
        return {"version": 1, "valid": False, "error_code": "SQL_PARSE_ERROR"}
    if isinstance(parsed, _FORBIDDEN_SQL_NODES):
        return {"version": 1, "valid": False, "error_code": "SQL_READONLY_REQUIRED"}
    if not isinstance(parsed, (exp.Select, exp.Union)):
        return {"version": 1, "valid": False, "error_code": "SQL_SELECT_REQUIRED"}
    cte_names = {str(cte.alias_or_name).lower() for cte in parsed.find_all(exp.CTE)}
    table_aliases: dict[str, str] = {}
    physical_tables: list[exp.Table] = []
    for table in parsed.find_all(exp.Table):
        table_name = table.name.lower()
        if table_name in cte_names and not table.db:
            continue
        physical_tables.append(table)
        table_aliases[table_name] = table_name
        if table.alias_or_name:
            table_aliases[str(table.alias_or_name).lower()] = table_name
    if not physical_tables:
        return {"version": 1, "valid": False, "error_code": "SQL_TABLE_REQUIRED"}

    for node in parsed.walk():
        if isinstance(node, (*_FORBIDDEN_SQL_NODES, exp.Lock, exp.Into, exp.Hint, exp.PropertyEQ)):
            return {"version": 1, "valid": False, "error_code": "SQL_READONLY_REQUIRED"}
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            return {"version": 1, "valid": False, "error_code": "SQL_STAR_NOT_ALLOWED"}
        if isinstance(node, exp.SessionParameter):
            return {"version": 1, "valid": False, "error_code": "SQL_DANGEROUS_FUNCTION"}
        if isinstance(node, exp.Func):
            function_name = str(node.sql_name() or node.name).lower()
            if function_name in _DANGEROUS_FUNCTIONS:
                return {"version": 1, "valid": False, "error_code": "SQL_DANGEROUS_FUNCTION"}
        if isinstance(node, exp.Anonymous) and str(node.name).lower() in _DANGEROUS_FUNCTIONS:
            return {"version": 1, "valid": False, "error_code": "SQL_DANGEROUS_FUNCTION"}
        if isinstance(node, exp.Table) and node in physical_tables:
            if node.catalog:
                return {"version": 1, "valid": False, "error_code": "SQL_CROSS_DATABASE_FORBIDDEN"}
            table_name = node.name
            schema_name = node.db or _default_schema(config)
            if config.sql_execution.database_type == "mysql" and schema_name.lower() in _MYSQL_SYSTEM_DATABASES:
                return {"version": 1, "valid": False, "error_code": "SQL_SYSTEM_DATABASE_FORBIDDEN"}
            qualified = f"{schema_name}.{table_name}"
            if not _allowed_object(schema_name, config.sql_execution.allowed_schemas) or not (_allowed_object(table_name, config.sql_execution.allowed_tables) or _allowed_object(qualified, config.sql_execution.allowed_tables)):
                return {"version": 1, "valid": False, "error_code": "SQL_TABLE_NOT_ALLOWED"}

    registry_tables: set[str] = set()
    registry_columns: set[str] = set()
    if request.source == "subagent":
        registry = retrieval.get("registry") if isinstance(retrieval, Mapping) else None
        registry = registry if isinstance(registry, Mapping) else {}
        for item in registry.values():
            if not isinstance(item, Mapping) or not isinstance(item.get("record"), Mapping):
                continue
            record = item["record"]
            table_name = record.get("table_name")
            if not isinstance(table_name, str) or not table_name.strip():
                continue
            table_name = table_name.strip().lower()
            if item.get("kind") == "table":
                registry_tables.add(table_name)
            if item.get("kind") == "column":
                registry_tables.add(table_name)
                column_name = record.get("column_name")
                if isinstance(column_name, str) and column_name.strip():
                    registry_columns.add(f"{table_name}.{column_name.strip().lower()}")
        if not registry_tables or not registry_columns:
            return {"version": 1, "valid": False, "error_code": "SQL_RETRIEVAL_REGISTRY_REQUIRED"}
    select_aliases = {str(expression.alias).lower() for select in parsed.find_all(exp.Select) for expression in select.expressions if isinstance(expression, exp.Alias) and expression.alias}
    for column in parsed.find_all(exp.Column):
        table = column.table.lower() if column.table else ""
        name = column.name
        if not table and name.lower() in select_aliases:
            continue
        actual_table = table_aliases.get(table, table)
        allowed_name = f"{actual_table}.{name}" if actual_table else name
        if not _allowed_object(name, config.sql_execution.allowed_columns) and not _allowed_object(allowed_name, config.sql_execution.allowed_columns):
            return {"version": 1, "valid": False, "error_code": "SQL_COLUMN_NOT_ALLOWED"}
        if request.source == "manual_ui" or table in cte_names:
            continue
        if actual_table and f"{actual_table}.{name.lower()}" not in registry_columns:
            return {"version": 1, "valid": False, "error_code": "SQL_COLUMN_NOT_IN_RETRIEVAL"}
        if not actual_table and not any(item.endswith(f".{name.lower()}") for item in registry_columns):
            return {"version": 1, "valid": False, "error_code": "SQL_COLUMN_NOT_IN_RETRIEVAL"}
    if request.source == "subagent":
        for table in physical_tables:
            if table.name.lower() not in registry_tables:
                return {"version": 1, "valid": False, "error_code": "SQL_TABLE_NOT_IN_RETRIEVAL"}

    executable = parsed.sql(dialect=_dialect(config), pretty=False).strip()
    existing_limit = parsed.args.get("limit")
    row_limit_applied = False
    if existing_limit is None:
        parsed = parsed.limit(config.sql_execution.max_rows)
        executable = parsed.sql(dialect=_dialect(config), pretty=False).strip()
        row_limit_applied = True
    elif isinstance(existing_limit, exp.Limit) and isinstance(existing_limit.expression, exp.Literal) and existing_limit.expression.is_number:
        if int(existing_limit.expression.this) > config.sql_execution.max_rows:
            existing_limit.set("expression", exp.Literal.number(config.sql_execution.max_rows))
            executable = parsed.sql(dialect=_dialect(config), pretty=False).strip()
            row_limit_applied = True
    binding_fingerprint = str(binding["binding_fingerprint"])
    return {
        "version": 1,
        "valid": True,
        "executable_sql": executable,
        "sql_sha256": sql_digest(executable),
        "validation_digest": sql_digest(f"{config.data_source_id}\n{config.sql_execution.database_type}\n{binding_fingerprint}\n{request.source}\n{request.snapshot_id or ''}\n{executable}"),
        "snapshot_id": request.snapshot_id,
        "database_type": config.sql_execution.database_type,
        "binding_fingerprint": binding_fingerprint,
        "row_limit_applied": row_limit_applied,
        "source": request.source,
    }


def _json_value(value: Any, *, max_chars: int) -> Any:
    """把数据库单元格转换成受预算保护的 JSON 值。

    Args:
        value: 数据库驱动返回的原始值。
        max_chars: 字符串单元格最大长度。

    Returns:
        JSON 可序列化的标量值。
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def _bounded_result(
    raw_rows: list[Any],
    columns: list[str],
    *,
    config: DataQueryServiceAbilityConfig,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    """统一处理数据库行并施加结果预算。

    Args:
        raw_rows: 数据库驱动返回的原始行。
        columns: 查询结果列名。
        config: 当前 DataAgent 查询能力配置。
        validation: 当前 SQL 校验结果。

    Returns:
        JSON 安全且受预算限制的查询结果。
    """
    capped_rows = raw_rows[: config.sql_execution.max_rows]
    values: list[dict[str, Any]] = []
    for raw_row in capped_rows:
        if isinstance(raw_row, Mapping):
            row = {str(key): _json_value(value, max_chars=config.sql_execution.max_cell_chars) for key, value in raw_row.items()}
        else:
            row = {columns[index] if index < len(columns) else f"column_{index + 1}": _json_value(value, max_chars=config.sql_execution.max_cell_chars) for index, value in enumerate(raw_row)}
        values.append(row)

    bounded_values: list[dict[str, Any]] = []
    used_chars = 2
    for row in values:
        row_chars = len(json.dumps(row, ensure_ascii=False, default=str)) + 1
        if used_chars + row_chars > config.sql_execution.max_result_chars:
            break
        bounded_values.append(row)
        used_chars += row_chars
    truncated = len(raw_rows) > config.sql_execution.max_rows or len(bounded_values) < len(values) or (validation.get("row_limit_applied") is True and len(values) >= config.sql_execution.max_rows)
    return {
        "row_count": len(values),
        "returned_row_count": len(bounded_values),
        "columns": columns,
        "rows": bounded_values,
        "truncated": truncated,
        "empty": not values,
    }


def _execute_postgres(sql: str, dsn: str, config: DataQueryServiceAbilityConfig) -> tuple[list[str], list[Any]]:
    """在 PostgreSQL 只读事务中执行 SQL。

    Args:
        sql: 已通过服务端校验的规范 SQL。
        dsn: 当前调用栈内解析的数据库 DSN。
        config: 当前 DataAgent 查询能力配置。

    Returns:
        列名和原始数据库行。
    """
    import psycopg

    with psycopg.connect(dsn, connect_timeout=config.sql_execution.statement_timeout_seconds) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT set_config('statement_timeout', %s, true)", (f"{config.sql_execution.statement_timeout_seconds * 1000}ms",))
            cursor.execute(sql)
            columns = [str(item.name) for item in cursor.description or []]
            rows = list(cursor.fetchmany(config.sql_execution.max_rows + 1))
    return columns, rows


def _execute_mysql(sql: str, dsn: str, config: DataQueryServiceAbilityConfig) -> tuple[list[str], list[Any]]:
    """在 MySQL 只读事务中执行 SQL。

    Args:
        sql: 已通过服务端校验的规范 SQL。
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
            cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {config.sql_execution.statement_timeout_seconds * 1000}")
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


def _safe_database_message(exc: Exception, *, dsn: str, include: bool) -> str | None:
    """提取可供 SQL 修复使用的安全数据库主错误信息。

    Args:
        exc: 数据库驱动异常。
        dsn: 当前调用使用的 DSN，用于精确脱敏。
        include: 当前错误类型是否允许向 SQL 模型暴露主错误信息。

    Returns:
        去除凭据、控制字符并限制长度的错误文本；不可暴露时返回 None。
    """
    if not include:
        return None
    diag = getattr(exc, "diag", None)
    raw = getattr(diag, "message_primary", None)
    if not isinstance(raw, str) or not raw.strip():
        args = getattr(exc, "args", ())
        if len(args) > 1 and isinstance(args[1], str):
            raw = args[1]
        elif args and isinstance(args[0], str):
            raw = args[0]
        else:
            raw = exc.__class__.__name__
    text = raw.replace(dsn, "[REDACTED]")
    text = _URI_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]@", text)
    text = " ".join(text.split())
    return text[:500]


def _classify_execution_error(exc: Exception, database_type: str, *, dsn: str) -> _ExecutionError:
    """把数据库驱动异常转换成稳定错误类别和重试建议。

    Args:
        exc: PostgreSQL 或 MySQL 驱动异常。
        database_type: 当前数据库类型。
        dsn: 当前调用使用的 DSN，仅用于错误文本脱敏。

    Returns:
        不包含异常堆栈和数据库凭据的错误合同。
    """
    category = "execution_error"
    error_code = "SQL_EXECUTION_FAILED"
    retryable = False
    recommended_action = "stop"

    if database_type == "postgresql":
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate == "57014":
            category = "timeout"
            error_code = "SQL_TIMEOUT"
            retryable = True
            recommended_action = "simplify_sql"
        elif sqlstate == "42601":
            category = "syntax_error"
            retryable = True
            recommended_action = "repair_sql"
        elif sqlstate == "42703":
            category = "unknown_column"
            retryable = True
            recommended_action = "repair_sql"
        elif sqlstate == "42P01":
            category = "unknown_table"
            retryable = True
            recommended_action = "repair_or_request_evidence"
        elif sqlstate == "42702":
            category = "ambiguous_column"
            retryable = True
            recommended_action = "repair_sql"
        elif sqlstate in {"42804", "42883"} or (isinstance(sqlstate, str) and sqlstate.startswith("22")):
            category = "type_mismatch"
            retryable = True
            recommended_action = "repair_sql"
        elif sqlstate == "42501":
            category = "permission_denied"
        elif isinstance(sqlstate, str) and sqlstate.startswith("08"):
            category = "connection_error"
            retryable = True
            recommended_action = "retry_same_sql"
    else:
        mysql_code = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], int) else None
        if mysql_code == 3024:
            category = "timeout"
            error_code = "SQL_TIMEOUT"
            retryable = True
            recommended_action = "simplify_sql"
        elif mysql_code == 1317:
            category = "cancelled"
            error_code = "SQL_CANCELLED"
        elif mysql_code == 1064:
            category = "syntax_error"
            retryable = True
            recommended_action = "repair_sql"
        elif mysql_code == 1054:
            category = "unknown_column"
            retryable = True
            recommended_action = "repair_sql"
        elif mysql_code == 1146:
            category = "unknown_table"
            retryable = True
            recommended_action = "repair_or_request_evidence"
        elif mysql_code == 1052:
            category = "ambiguous_column"
            retryable = True
            recommended_action = "repair_sql"
        elif mysql_code in {1241, 1242, 1264, 1292, 1366}:
            category = "type_mismatch"
            retryable = True
            recommended_action = "repair_sql"
        elif mysql_code in {1044, 1045, 1142, 1227}:
            category = "permission_denied"
        elif mysql_code in {2002, 2003, 2006, 2013}:
            category = "connection_error"
            retryable = True
            recommended_action = "retry_same_sql"

    if category == "execution_error" and isinstance(exc, (ConnectionError, TimeoutError)):
        category = "connection_error"
        retryable = True
        recommended_action = "retry_same_sql"
    include_message = category in {"syntax_error", "unknown_column", "unknown_table", "ambiguous_column", "type_mismatch"}
    return _ExecutionError(
        error_code=error_code,
        category=category,
        retryable=retryable,
        recommended_action=recommended_action,
        message=_safe_database_message(exc, dsn=dsn, include=include_message),
    )


class SqlExecutionService:
    """共享 SQL 校验与只读执行服务。

    Gateway API 和 SQL SubAgent 应调用同一 Service，避免维护两套数据库执行逻辑。
    """

    def __init__(
        self,
        config: DataQueryServiceAbilityConfig,
        *,
        env: Mapping[str, str] | None = None,
        secrets: Mapping[str, str] | None = None,
    ) -> None:
        """初始化 SQL Executor。

        Args:
            config: 当前 DataAgent 查询能力配置。
            env: 进程环境变量映射；测试可注入隔离环境。
            secrets: 当前请求上下文中的短期 Secret。
        """
        self._config = config
        self._env = env if env is not None else os.environ
        self._secrets = secrets

    @property
    def config(self) -> DataQueryServiceAbilityConfig:
        """返回当前只读配置。

        Returns:
            当前 DataAgent 查询能力配置。
        """
        return self._config

    def validate(self, request: SqlValidationRequest) -> SqlValidationResult:
        """校验 SQL 并生成绑定当前 Snapshot 的规范 SQL。

        Args:
            request: SQL 校验请求。

        Returns:
            版本化 SQL 校验结果。
        """
        manual_binding: Mapping[str, Any] | None = None
        if request.source == "manual_ui":
            try:
                manual_binding = resolve_data_source_binding(
                    self._config,
                    env=self._env,
                    secrets=self._secrets,
                )
            except ValueError:
                return {"version": 1, "valid": False, "error_code": "SQL_BINDING_MISMATCH"}
        return _validate_sql(request, config=self._config, manual_binding=manual_binding)

    def execute(self, request: SqlExecutionRequest) -> SqlExecutionResult:
        """执行最近一次已校验 SQL。

        Args:
            request: SQL 执行请求。

        Returns:
            JSON 安全的原始数据库结果或结构化错误。
        """
        started_at = time.perf_counter()
        validation = request.validation
        database_type = self._config.sql_execution.database_type
        result_identity = {
            "database_type": database_type,
            "sql_sha256": validation.get("sql_sha256"),
            "validation_digest": validation.get("validation_digest"),
        }
        if validation.get("valid") is not True or validation.get("sql_sha256") != sql_digest(request.sql) or validation.get("validation_digest") != request.validation_digest or validation.get("source") != request.source:
            return {
                "version": 1,
                "ok": False,
                "error_code": "SQL_DIGEST_MISMATCH",
                "error_category": "validation_error",
                "retryable": True,
                "recommended_action": "revalidate_sql",
                "duration_ms": 0,
                **result_identity,
            }
        try:
            dsn = resolve_execution_dsn(self._config, self._env, self._secrets)
        except ValueError:
            return {
                "version": 1,
                "ok": False,
                "error_code": "SQL_DSN_MISSING",
                "error_category": "configuration_error",
                "retryable": False,
                "recommended_action": "stop",
                "duration_ms": 0,
                **result_identity,
            }
        try:
            current_binding = resolve_data_source_binding(
                self._config,
                env=self._env,
                secrets=self._secrets,
            )
        except ValueError:
            return {
                "version": 1,
                "ok": False,
                "error_code": "SQL_BINDING_MISMATCH",
                "error_category": "binding_error",
                "retryable": False,
                "recommended_action": "stop",
                "duration_ms": 0,
                **result_identity,
            }
        if validation.get("database_type") != database_type or validation.get("binding_fingerprint") != current_binding.get("binding_fingerprint"):
            return {
                "version": 1,
                "ok": False,
                "error_code": "SQL_BINDING_MISMATCH",
                "error_category": "binding_error",
                "retryable": False,
                "recommended_action": "stop",
                "duration_ms": 0,
                **result_identity,
            }
        try:
            if database_type == "mysql":
                columns, rows = _execute_mysql(request.sql, dsn, self._config)
            else:
                columns, rows = _execute_postgres(request.sql, dsn, self._config)
            bounded = _bounded_result(rows, columns, config=self._config, validation=validation)
            return {
                "version": 1,
                "ok": True,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                **bounded,
                **result_identity,
            }
        except Exception as exc:
            error = _classify_execution_error(exc, database_type, dsn=dsn)
            result: SqlExecutionResult = {
                "version": 1,
                "ok": False,
                "error_code": error.error_code,
                "error_category": error.category,
                "retryable": error.retryable,
                "recommended_action": error.recommended_action,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                **result_identity,
            }
            if error.message is not None:
                result["error_message"] = error.message
            return result

    async def aexecute(self, request: SqlExecutionRequest) -> SqlExecutionResult:
        """在线程池中执行同步数据库调用。

        Args:
            request: SQL 执行请求。

        Returns:
            JSON 安全的原始数据库结果或结构化错误。
        """
        return await asyncio.to_thread(self.execute, request)


def validate_sql(
    sql: str,
    *,
    config: DataQueryServiceAbilityConfig,
    retrieval: Mapping[str, Any],
    snapshot_id: str | None = None,
) -> SqlValidationResult:
    """使用共享 SQL Executor 校验候选 SQL。

    Args:
        sql: SQL 模型生成的候选 SQL。
        config: 当前 DataAgent 查询能力配置。
        retrieval: 当前 Query Snapshot 的 TableRAG registry。
        snapshot_id: 当前已批准 Query Snapshot 标识。

    Returns:
        版本化 SQL 校验结果。
    """
    return SqlExecutionService(config).validate(
        SqlValidationRequest(
            sql=sql,
            retrieval=retrieval,
            snapshot_id=snapshot_id,
        )
    )


def execute_sql(
    sql: str,
    *,
    validation_digest: str,
    validation: Mapping[str, Any],
    config: DataQueryServiceAbilityConfig,
    secrets: Mapping[str, str] | None = None,
) -> SqlExecutionResult:
    """使用共享 SQL Executor 执行已校验 SQL。

    Args:
        sql: ``validate_sql`` 返回的规范 SQL。
        validation_digest: 同一次校验返回的服务端摘要。
        validation: 最近一次 SQL 校验结果。
        config: 当前 DataAgent 查询能力配置。
        secrets: 当前请求上下文中的短期数据库 Secret。

    Returns:
        JSON 安全的原始数据库结果或结构化错误。
    """
    return SqlExecutionService(config, secrets=secrets).execute(
        SqlExecutionRequest(
            sql=sql,
            validation_digest=validation_digest,
            validation=validation,
        )
    )
