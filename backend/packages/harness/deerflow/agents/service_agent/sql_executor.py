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
_MYSQL_SYSTEM_DATABASES = frozenset(
    {"information_schema", "mysql", "performance_schema", "sys"}
)
_MYSQL_EXECUTABLE_COMMENT_PATTERN = re.compile(
    r"/\*!\s*\d{0,6}\s*", flags=re.IGNORECASE
)
_URI_CREDENTIAL_PATTERN = re.compile(
    r"\b([a-z][a-z0-9+.-]*://)([^@\s/]+)@", flags=re.IGNORECASE
)
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

# 错误映射统一放在表中，避免大量重复的 if/elif。
_POSTGRES_ERROR_RULES = {
    "57014": ("timeout", "SQL_TIMEOUT", True, "simplify_sql"),
    "42601": ("syntax_error", "SQL_EXECUTION_FAILED", True, "repair_sql"),
    "42703": ("unknown_column", "SQL_EXECUTION_FAILED", True, "repair_sql"),
    "42P01": (
        "unknown_table",
        "SQL_EXECUTION_FAILED",
        True,
        "repair_or_request_evidence",
    ),
    "42702": ("ambiguous_column", "SQL_EXECUTION_FAILED", True, "repair_sql"),
    "42501": ("permission_denied", "SQL_EXECUTION_FAILED", False, "stop"),
}
_MYSQL_ERROR_RULES = {
    3024: ("timeout", "SQL_TIMEOUT", True, "simplify_sql"),
    1317: ("cancelled", "SQL_CANCELLED", False, "stop"),
    1064: ("syntax_error", "SQL_EXECUTION_FAILED", True, "repair_sql"),
    1054: ("unknown_column", "SQL_EXECUTION_FAILED", True, "repair_sql"),
    1146: (
        "unknown_table",
        "SQL_EXECUTION_FAILED",
        True,
        "repair_or_request_evidence",
    ),
    1052: ("ambiguous_column", "SQL_EXECUTION_FAILED", True, "repair_sql"),
    1044: ("permission_denied", "SQL_EXECUTION_FAILED", False, "stop"),
    1045: ("permission_denied", "SQL_EXECUTION_FAILED", False, "stop"),
    1142: ("permission_denied", "SQL_EXECUTION_FAILED", False, "stop"),
    1227: ("permission_denied", "SQL_EXECUTION_FAILED", False, "stop"),
    2002: ("connection_error", "SQL_EXECUTION_FAILED", True, "retry_same_sql"),
    2003: ("connection_error", "SQL_EXECUTION_FAILED", True, "retry_same_sql"),
    2006: ("connection_error", "SQL_EXECUTION_FAILED", True, "retry_same_sql"),
    2013: ("connection_error", "SQL_EXECUTION_FAILED", True, "retry_same_sql"),
}


class SqlValidationResult(TypedDict, total=False):
    """SQL 校验结果。"""

    version: int
    valid: bool
    error_code: str
    error_message: str
    executable_sql: str
    sql_sha256: str
    validation_digest: str
    snapshot_id: str | None
    database_type: str
    binding_fingerprint: str
    row_limit_applied: bool
    source: SqlExecutionSource


class SqlExecutionResult(TypedDict, total=False):
    """SQL 执行结果。"""

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
    """SQL 校验请求。"""

    sql: str
    retrieval: Mapping[str, Any] | None = None
    snapshot_id: str | None = None
    source: SqlExecutionSource = "subagent"


@dataclass(frozen=True, slots=True)
class SqlExecutionRequest:
    """SQL 执行请求。"""

    sql: str
    validation_digest: str
    validation: Mapping[str, Any]
    source: SqlExecutionSource = "subagent"


@dataclass(frozen=True, slots=True)
class _ExecutionError:
    """数据库异常的结构化分类。"""

    error_code: str
    category: str
    retryable: bool
    recommended_action: str
    message: str | None = None


def _validation_error(code: str, **extra: Any) -> SqlValidationResult:
    """构造统一的校验失败结果。"""
    return {"version": 1, "valid": False, "error_code": code, **extra}


def _execution_error(
    identity: Mapping[str, Any],
    *,
    code: str,
    category: str,
    retryable: bool,
    action: str,
    duration_ms: float = 0,
    message: str | None = None,
) -> SqlExecutionResult:
    """构造统一的执行失败结果。"""
    result: SqlExecutionResult = {
        "version": 1,
        "ok": False,
        "error_code": code,
        "error_category": category,
        "retryable": retryable,
        "recommended_action": action,
        "duration_ms": duration_ms,
        **identity,
    }
    if message is not None:
        result["error_message"] = message
    return result


def sql_digest(sql: str) -> str:
    """计算 SQL 的 SHA-256 摘要。"""
    return f"sha256:{sha256(sql.encode('utf-8')).hexdigest()}"


def _is_allowed_schema(name: str, allowed_schemas: list[str]) -> bool:
    """大小写不敏感地检查 Schema 是否在白名单中。"""
    name = name.lower()
    return bool(allowed_schemas) and any(
        item.lower() == name for item in allowed_schemas
    )


def _dialect(config: DataQueryServiceAbilityConfig) -> str:
    """返回 sqlglot 方言名。"""
    return "mysql" if config.sql_execution.database_type == "mysql" else "postgres"


def _default_schema(config: DataQueryServiceAbilityConfig) -> str:
    """返回未限定表名时使用的默认 Schema。"""
    return (
        config.sql_execution.allowed_schemas[0]
        if config.sql_execution.database_type == "mysql"
        else "public"
    )


def _validate_sql(
    request: SqlValidationRequest,
    *,
    config: DataQueryServiceAbilityConfig,
    manual_binding: Mapping[str, Any] | None = None,
) -> SqlValidationResult:
    """校验只读 SQL、数据源绑定和 SubAgent 证据。"""
    execution = config.sql_execution
    dialect = _dialect(config)
    sql = request.sql
    retrieval = request.retrieval

    if request.source not in {"subagent", "manual_ui"}:
        return _validation_error("SQL_SOURCE_INVALID")
    if not isinstance(sql, str) or not sql.strip():
        return _validation_error("SQL_EMPTY")
    if execution.database_type == "mysql" and _MYSQL_EXECUTABLE_COMMENT_PATTERN.search(sql):
        return _validation_error("SQL_EXECUTABLE_COMMENT_FORBIDDEN")
    if not execution.allowed_schemas:
        return _validation_error("SQL_SCHEMA_REQUIRED")

    binding = (
        manual_binding
        if request.source == "manual_ui"
        else retrieval.get("binding") if isinstance(retrieval, Mapping) else None
    )
    if not isinstance(binding, Mapping) or not isinstance(
        binding.get("binding_fingerprint"), str
    ):
        return _validation_error("SQL_BINDING_REQUIRED")
    if (
        binding.get("data_source_id") != config.data_source_id
        or binding.get("database_type") != execution.database_type
    ):
        return _validation_error("SQL_BINDING_MISMATCH")

    try:
        statements = [
            statement for statement in parse(sql, read=dialect) if statement is not None
        ]
    except ParseError:
        return _validation_error("SQL_PARSE_ERROR")
    if len(statements) != 1:
        return _validation_error("SQL_MULTIPLE_STATEMENTS")

    parsed = statements[0]
    if not isinstance(parsed, exp.Expression):
        return _validation_error("SQL_PARSE_ERROR")
    if isinstance(parsed, _FORBIDDEN_SQL_NODES):
        return _validation_error("SQL_READONLY_REQUIRED")
    if not isinstance(parsed, (exp.Select, exp.Union)):
        return _validation_error("SQL_SELECT_REQUIRED")

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
        return _validation_error("SQL_TABLE_REQUIRED")

    # AST 级只读检查、危险函数检查和 Schema 检查。
    for node in parsed.walk():
        if isinstance(
            node,
            (*_FORBIDDEN_SQL_NODES, exp.Lock, exp.Into, exp.Hint, exp.PropertyEQ),
        ):
            return _validation_error("SQL_READONLY_REQUIRED")
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            return _validation_error("SQL_STAR_NOT_ALLOWED")
        if isinstance(node, exp.SessionParameter):
            return _validation_error("SQL_DANGEROUS_FUNCTION")
        if isinstance(node, exp.Func):
            function_name = str(node.sql_name() or node.name).lower()
            if function_name in _DANGEROUS_FUNCTIONS:
                return _validation_error("SQL_DANGEROUS_FUNCTION")
        if isinstance(node, exp.Anonymous) and str(node.name).lower() in _DANGEROUS_FUNCTIONS:
            return _validation_error("SQL_DANGEROUS_FUNCTION")
        if isinstance(node, exp.Table) and node in physical_tables:
            if node.catalog:
                return _validation_error("SQL_CROSS_DATABASE_FORBIDDEN")
            schema_name = node.db or _default_schema(config)
            if (
                execution.database_type == "mysql"
                and schema_name.lower() in _MYSQL_SYSTEM_DATABASES
            ):
                return _validation_error("SQL_SYSTEM_DATABASE_FORBIDDEN")
            if not _is_allowed_schema(schema_name, execution.allowed_schemas):
                return _validation_error(
                    "SQL_SCHEMA_NOT_ALLOWED",
                    error_message=f"Schema `{schema_name}` 未配置在 sql_execution.allowed_schemas 中。",
                )

    registry_tables: set[str] = set()
    registry_columns: set[str] = set()
    if request.source == "subagent":
        registry = retrieval.get("registry") if isinstance(retrieval, Mapping) else {}
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
            elif item.get("kind") == "column":
                registry_tables.add(table_name)
                column_name = record.get("column_name")
                if isinstance(column_name, str) and column_name.strip():
                    registry_columns.add(f"{table_name}.{column_name.strip().lower()}")
        if not registry_tables or not registry_columns:
            return _validation_error("SQL_RETRIEVAL_REGISTRY_REQUIRED")

    select_aliases = {
        str(expression.alias).lower()
        for select in parsed.find_all(exp.Select)
        for expression in select.expressions
        if isinstance(expression, exp.Alias) and expression.alias
    }
    for column in parsed.find_all(exp.Column):
        table = column.table.lower() if column.table else ""
        name = column.name.lower()
        if not table and name in select_aliases:
            continue
        actual_table = table_aliases.get(table, table)
        if request.source == "manual_ui" or table in cte_names:
            continue
        if actual_table and f"{actual_table}.{name}" not in registry_columns:
            return _validation_error("SQL_COLUMN_NOT_IN_RETRIEVAL")
        if not actual_table and not any(
            item.endswith(f".{name}") for item in registry_columns
        ):
            return _validation_error("SQL_COLUMN_NOT_IN_RETRIEVAL")

    if request.source == "subagent":
        for table in physical_tables:
            if table.name.lower() not in registry_tables:
                return _validation_error("SQL_TABLE_NOT_IN_RETRIEVAL")

    # 没有限制或限制过大时，统一补上最大行数。
    row_limit_applied = False
    limit = parsed.args.get("limit")
    if limit is None:
        parsed = parsed.limit(execution.max_rows)
        row_limit_applied = True
    elif (
        isinstance(limit, exp.Limit)
        and isinstance(limit.expression, exp.Literal)
        and limit.expression.is_number
        and int(limit.expression.this) > execution.max_rows
    ):
        limit.set("expression", exp.Literal.number(execution.max_rows))
        row_limit_applied = True

    executable = parsed.sql(dialect=dialect, pretty=False).strip()
    binding_fingerprint = str(binding["binding_fingerprint"])
    return {
        "version": 1,
        "valid": True,
        "executable_sql": executable,
        "sql_sha256": sql_digest(executable),
        "validation_digest": sql_digest(
            f"{config.data_source_id}\n{execution.database_type}\n"
            f"{binding_fingerprint}\n{request.source}\n"
            f"{request.snapshot_id or ''}\n{executable}"
        ),
        "snapshot_id": request.snapshot_id,
        "database_type": execution.database_type,
        "binding_fingerprint": binding_fingerprint,
        "row_limit_applied": row_limit_applied,
        "source": request.source,
    }


def _json_value(value: Any, *, max_chars: int) -> Any:
    """把数据库单元格转换成受长度限制的 JSON 值。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def _bounded_result(
    raw_rows: list[Any],
    columns: list[str],
    *,
    config: DataQueryServiceAbilityConfig,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    """转换数据库结果，并限制行数、单元格长度和总字符数。"""
    execution = config.sql_execution
    values = []
    for raw_row in raw_rows[: execution.max_rows]:
        if isinstance(raw_row, Mapping):
            row = {
                str(key): _json_value(value, max_chars=execution.max_cell_chars)
                for key, value in raw_row.items()
            }
        else:
            row = {
                columns[index] if index < len(columns) else f"column_{index + 1}": _json_value(
                    value, max_chars=execution.max_cell_chars
                )
                for index, value in enumerate(raw_row)
            }
        values.append(row)

    bounded_values = []
    used_chars = 2
    for row in values:
        row_chars = len(json.dumps(row, ensure_ascii=False, default=str)) + 1
        if used_chars + row_chars > execution.max_result_chars:
            break
        bounded_values.append(row)
        used_chars += row_chars

    truncated = (
        len(raw_rows) > execution.max_rows
        or len(bounded_values) < len(values)
        or (
            validation.get("row_limit_applied") is True
            and len(values) >= execution.max_rows
        )
    )
    return {
        "row_count": len(values),
        "returned_row_count": len(bounded_values),
        "columns": columns,
        "rows": bounded_values,
        "truncated": truncated,
        "empty": not values,
    }


def _execute_postgres(
    sql: str, dsn: str, config: DataQueryServiceAbilityConfig
) -> tuple[list[str], list[Any]]:
    """在 PostgreSQL 只读事务中执行 SQL。"""
    import psycopg

    timeout = config.sql_execution.statement_timeout_seconds
    with psycopg.connect(dsn, connect_timeout=timeout) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{timeout * 1000}ms",),
            )
            cursor.execute(sql)
            columns = [str(item.name) for item in cursor.description or []]
            rows = list(cursor.fetchmany(config.sql_execution.max_rows + 1))
    return columns, rows


def _execute_mysql(
    sql: str, dsn: str, config: DataQueryServiceAbilityConfig
) -> tuple[list[str], list[Any]]:
    """在 MySQL 只读事务中执行 SQL。"""
    import pymysql

    parsed = urlsplit(dsn)
    if parsed.scheme.lower() not in {"mysql", "mysql+pymysql"}:
        raise ValueError("SQL_DSN_SCHEME_INVALID")

    timeout = config.sql_execution.statement_timeout_seconds
    query = parse_qs(parsed.query)
    connection = pymysql.connect(
        host=parsed.hostname or "",
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=unquote(parsed.path.lstrip("/")),
        charset=(query.get("charset") or ["utf8mb4"])[0],
        connect_timeout=timeout,
        read_timeout=timeout,
        write_timeout=timeout,
        autocommit=False,
        local_infile=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {timeout * 1000}")
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


def _safe_database_message(exc: Exception, *, dsn: str) -> str:
    """提取脱敏后的数据库主错误信息。"""
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
    return " ".join(text.split())[:500]


def _classify_execution_error(
    exc: Exception, database_type: str, *, dsn: str
) -> _ExecutionError:
    """把数据库异常转换成稳定错误类别和重试建议。"""
    default = ("execution_error", "SQL_EXECUTION_FAILED", False, "stop")
    rule = default

    if database_type == "postgresql":
        sqlstate = getattr(exc, "sqlstate", None)
        if isinstance(sqlstate, str) and sqlstate.startswith("22"):
            rule = ("type_mismatch", "SQL_EXECUTION_FAILED", True, "repair_sql")
        elif isinstance(sqlstate, str) and sqlstate.startswith("08"):
            rule = ("connection_error", "SQL_EXECUTION_FAILED", True, "retry_same_sql")
        elif isinstance(sqlstate, str):
            rule = _POSTGRES_ERROR_RULES.get(sqlstate, default)
        if rule == default and isinstance(exc, (ConnectionError, TimeoutError)):
            rule = ("connection_error", "SQL_EXECUTION_FAILED", True, "retry_same_sql")
    else:
        args = getattr(exc, "args", ())
        mysql_code = args[0] if args and isinstance(args[0], int) else None
        if mysql_code in {1241, 1242, 1264, 1292, 1366}:
            rule = ("type_mismatch", "SQL_EXECUTION_FAILED", True, "repair_sql")
        elif mysql_code is not None:
            rule = _MYSQL_ERROR_RULES.get(mysql_code, default)
        if rule == default and isinstance(exc, (ConnectionError, TimeoutError)):
            rule = ("connection_error", "SQL_EXECUTION_FAILED", True, "retry_same_sql")

    category, error_code, retryable, action = rule
    return _ExecutionError(
        error_code=error_code,
        category=category,
        retryable=retryable,
        recommended_action=action,
        message=_safe_database_message(exc, dsn=dsn),
    )


class SqlExecutionService:
    """共享 SQL 校验与只读执行服务。"""

    def __init__(
        self,
        config: DataQueryServiceAbilityConfig,
        *,
        env: Mapping[str, str] | None = None,
        secrets: Mapping[str, str] | None = None,
    ) -> None:
        self._config = config
        self._env = env if env is not None else os.environ
        self._secrets = secrets

    @property
    def config(self) -> DataQueryServiceAbilityConfig:
        """返回当前配置。"""
        return self._config

    def validate(self, request: SqlValidationRequest) -> SqlValidationResult:
        """校验 SQL 并生成绑定当前 Snapshot 的规范 SQL。"""
        manual_binding: Mapping[str, Any] | None = None
        if request.source == "manual_ui":
            try:
                manual_binding = resolve_data_source_binding(
                    self._config,
                    env=self._env,
                    secrets=self._secrets,
                )
            except ValueError:
                return _validation_error("SQL_BINDING_MISMATCH")
        return _validate_sql(
            request, config=self._config, manual_binding=manual_binding
        )

    def execute(self, request: SqlExecutionRequest) -> SqlExecutionResult:
        """执行最近一次已校验 SQL。"""
        started_at = time.perf_counter()
        validation = request.validation
        database_type = self._config.sql_execution.database_type
        identity = {
            "database_type": database_type,
            "sql_sha256": validation.get("sql_sha256"),
            "validation_digest": validation.get("validation_digest"),
        }

        if (
            validation.get("valid") is not True
            or validation.get("sql_sha256") != sql_digest(request.sql)
            or validation.get("validation_digest") != request.validation_digest
            or validation.get("source") != request.source
        ):
            return _execution_error(
                identity,
                code="SQL_DIGEST_MISMATCH",
                category="validation_error",
                retryable=True,
                action="revalidate_sql",
            )

        try:
            dsn = resolve_execution_dsn(self._config, self._env, self._secrets)
        except ValueError:
            return _execution_error(
                identity,
                code="SQL_DSN_MISSING",
                category="configuration_error",
                retryable=False,
                action="stop",
            )

        try:
            current_binding = resolve_data_source_binding(
                self._config,
                env=self._env,
                secrets=self._secrets,
            )
        except ValueError:
            return _execution_error(
                identity,
                code="SQL_BINDING_MISMATCH",
                category="binding_error",
                retryable=False,
                action="stop",
            )

        if (
            validation.get("database_type") != database_type
            or validation.get("binding_fingerprint")
            != current_binding.get("binding_fingerprint")
        ):
            return _execution_error(
                identity,
                code="SQL_BINDING_MISMATCH",
                category="binding_error",
                retryable=False,
                action="stop",
            )

        try:
            executor = _execute_mysql if database_type == "mysql" else _execute_postgres
            columns, rows = executor(request.sql, dsn, self._config)
            return {
                "version": 1,
                "ok": True,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                **_bounded_result(
                    rows, columns, config=self._config, validation=validation
                ),
                **identity,
            }
        except Exception as exc:
            error = _classify_execution_error(exc, database_type, dsn=dsn)
            return _execution_error(
                identity,
                code=error.error_code,
                category=error.category,
                retryable=error.retryable,
                action=error.recommended_action,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                message=error.message,
            )

    async def aexecute(self, request: SqlExecutionRequest) -> SqlExecutionResult:
        """在线程池中执行同步数据库调用。"""
        return await asyncio.to_thread(self.execute, request)


def validate_sql(
    sql: str,
    *,
    config: DataQueryServiceAbilityConfig,
    retrieval: Mapping[str, Any],
    snapshot_id: str | None = None,
) -> SqlValidationResult:
    """使用共享服务校验候选 SQL。"""
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
    """使用共享服务执行已校验 SQL。"""
    return SqlExecutionService(config, secrets=secrets).execute(
        SqlExecutionRequest(
            sql=sql,
            validation_digest=validation_digest,
            validation=validation,
        )
    )
