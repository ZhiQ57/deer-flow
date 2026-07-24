"""DataAgent PostgreSQL/MySQL 只读 SQL 工具和 AST 校验。"""

# ADD: DataAgent 正式查询闭环新增，SQL 工具仅由 sql-subagent 显式 allowlist 装配。
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from hashlib import sha256
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from langchain_core.tools import BaseTool, StructuredTool
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .config import DataQueryServiceAbilityConfig

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
# ADD: 在整棵 AST 中阻断写操作、DDL、权限和会话控制节点，覆盖 PostgreSQL 可写 CTE 等嵌套形式。
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


# ADD: 生成 SQL/快照 digest，执行工具只接受最近一次校验的规范 SQL。
def sql_digest(sql: str) -> str:
    """计算规范化 SQL 的 sha256 digest。"""
    return f"sha256:{sha256(sql.encode('utf-8')).hexdigest()}"


def _json_content(value: Mapping[str, Any]) -> str:
    """序列化工具结果，避免把内部异常对象写入消息。"""
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, default=str)


def _allowed_object(name: str, allowlist: list[str]) -> bool:
    """按大小写不敏感方式匹配 schema/table/column allowlist。"""
    if not allowlist:
        return False
    lowered = name.lower()
    return any(item.lower() == lowered for item in allowlist)


def _dialect(config: DataQueryServiceAbilityConfig) -> str:
    """返回 sqlglot 使用的唯一方言名。"""
    return "mysql" if config.sql_execution.database_type == "mysql" else "postgres"


def _default_schema(config: DataQueryServiceAbilityConfig) -> str:
    """返回未限定表名在当前执行源中的默认 Schema/Database。"""
    if config.sql_execution.database_type == "mysql":
        return config.sql_execution.allowed_schemas[0]
    return "public"


# ADD: 基于 sqlglot AST 校验单条 PostgreSQL/MySQL SELECT/WITH。
def validate_sql(
    sql: str,
    *,
    config: DataQueryServiceAbilityConfig,
    retrieval: Mapping[str, Any],
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """校验只读 SQL 并返回规范 executable_sql。

    Args:
        sql: 模型生成的 SQL 草稿。
        config: 当前 DataAgent SQL 执行配置。
        retrieval: 当前 snapshot 的 TableRAG registry。

    Returns:
        版本化校验结果。
    """
    if not isinstance(sql, str) or not sql.strip():
        return {"version": 1, "valid": False, "error_code": "SQL_EMPTY"}
    if config.sql_execution.database_type == "mysql" and _MYSQL_EXECUTABLE_COMMENT_PATTERN.search(sql):
        return {"version": 1, "valid": False, "error_code": "SQL_EXECUTABLE_COMMENT_FORBIDDEN"}
    if not config.sql_execution.allowed_schemas or not config.sql_execution.allowed_tables or not config.sql_execution.allowed_columns:
        return {"version": 1, "valid": False, "error_code": "SQL_ALLOWLIST_REQUIRED"}
    binding = retrieval.get("binding") if isinstance(retrieval, Mapping) else None
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
    # ADD: parse_one 只允许一条语句；显式阻断 DML/DDL/COPY/事务节点。
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
    # ADD: SQL 必须实际读取当前检索登记的业务表，禁止 SELECT 常量或数据库会话信息绕过 Evidence 约束。
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

    registry = retrieval.get("registry") if isinstance(retrieval, Mapping) else None
    registry = registry if isinstance(registry, Mapping) else {}
    registry_tables: set[str] = set()
    registry_columns: set[str] = set()
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
        if table in cte_names:
            continue
        if actual_table and f"{actual_table}.{name.lower()}" not in registry_columns:
            return {"version": 1, "valid": False, "error_code": "SQL_COLUMN_NOT_IN_RETRIEVAL"}
        if not actual_table and not any(item.endswith(f".{name.lower()}") for item in registry_columns):
            return {"version": 1, "valid": False, "error_code": "SQL_COLUMN_NOT_IN_RETRIEVAL"}
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
        "validation_digest": sql_digest(f"{config.data_source_id}\n{config.sql_execution.database_type}\n{binding_fingerprint}\n{snapshot_id or ''}\n{executable}"),
        "snapshot_id": snapshot_id,
        "database_type": config.sql_execution.database_type,
        "binding_fingerprint": binding_fingerprint,
        "row_limit_applied": row_limit_applied,
    }


def _json_value(value: Any, *, max_chars: int) -> Any:
    """把数据库值转换成受单元格预算保护的 JSON 值。"""
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
    """统一规范 PostgreSQL/MySQL 行，并施加行数、单元格和总字符预算。"""
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
    """在 PostgreSQL 只读事务中执行规范 SQL。"""
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
    """在 MySQL 只读事务中执行规范 SQL。"""
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


def _execution_error_code(exc: Exception, database_type: str) -> str:
    """把驱动异常映射为稳定错误码，不返回原始异常文本。"""
    if database_type == "postgresql":
        return "SQL_TIMEOUT" if getattr(exc, "sqlstate", None) == "57014" else "SQL_EXECUTION_FAILED"
    mysql_code = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], int) else None
    if mysql_code == 3024:
        return "SQL_TIMEOUT"
    if mysql_code == 1317:
        return "SQL_CANCELLED"
    return "SQL_EXECUTION_FAILED"


# ADD: 在只读事务中执行已校验的 PostgreSQL/MySQL SQL，并执行结果预算。
def execute_sql(
    sql: str,
    *,
    validation_digest: str,
    validation: Mapping[str, Any],
    config: DataQueryServiceAbilityConfig,
    secrets: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """执行规范 SQL，错误仅返回稳定安全错误码。"""
    started_at = time.perf_counter()
    if validation.get("valid") is not True or validation.get("sql_sha256") != sql_digest(sql) or validation.get("validation_digest") != validation_digest:
        return {"version": 1, "ok": False, "error_code": "SQL_DIGEST_MISMATCH"}
    result_identity = {
        "sql_sha256": validation.get("sql_sha256"),
        "validation_digest": validation.get("validation_digest"),
    }
    try:
        # ADD: DSN 只在当前执行调用栈解析，Secret 值不进入状态、artifact 或工具返回。
        from .binding import resolve_execution_dsn

        dsn = resolve_execution_dsn(config, os.environ, secrets)
    except ValueError:
        return {"version": 1, "ok": False, "error_code": "SQL_DSN_MISSING", "duration_ms": 0, **result_identity}
    try:
        # ADD: 执行前重新解析服务端绑定，阻止环境变量或目标库切换后复用旧校验结果。
        from .binding import resolve_data_source_binding

        current_binding = resolve_data_source_binding(config, secrets=secrets)
    except ValueError:
        return {"version": 1, "ok": False, "error_code": "SQL_BINDING_MISMATCH", "duration_ms": 0, **result_identity}
    if validation.get("database_type") != config.sql_execution.database_type or validation.get("binding_fingerprint") != current_binding.get("binding_fingerprint"):
        return {"version": 1, "ok": False, "error_code": "SQL_BINDING_MISMATCH", "duration_ms": 0, **result_identity}
    try:
        if config.sql_execution.database_type == "mysql":
            columns, rows = _execute_mysql(sql, dsn, config)
        else:
            columns, rows = _execute_postgres(sql, dsn, config)
        bounded = _bounded_result(rows, columns, config=config, validation=validation)
        return {
            "version": 1,
            "ok": True,
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            **bounded,
            **result_identity,
        }
    except Exception as exc:
        error_code = _execution_error_code(exc, config.sql_execution.database_type)
        return {"version": 1, "ok": False, "error_code": error_code, "duration_ms": round((time.perf_counter() - started_at) * 1000, 2), **result_identity}


# ADD: 为 sql-subagent 生成带当前快照闭包的两个显式工具，父 lead-agent 不会看到它们。
def build_sql_tools(
    config: DataQueryServiceAbilityConfig,
    service_state: Mapping[str, Any],
    *,
    secrets: Mapping[str, str] | None = None,
) -> list[BaseTool]:
    """构造仅绑定当前 DataAgent 快照的 SQL 工具。"""
    payload = service_state.get("payload") if isinstance(service_state, Mapping) else None
    retrieval = payload.get("retrieval") if isinstance(payload, Mapping) else None
    retrieval = retrieval if isinstance(retrieval, Mapping) else {}
    approval = payload.get("approval") if isinstance(payload, Mapping) else None
    action = approval.get("action") if isinstance(approval, Mapping) and approval.get("status") == "approved" else None
    if action not in {"execute", "sql_only"}:
        # ADD: 缺失或未批准的动作不向 SQL SubAgent 暴露任何 SQL 工具，继续保持 fail closed。
        return []
    validation_holder: dict[str, Any] = {}
    execution_attempted = False
    execution_lock = Lock()
    snapshot_id = str(service_state.get("snapshot_id") or "")

    def validate(sql: str) -> str:
        result = validate_sql(sql, config=config, retrieval=retrieval, snapshot_id=snapshot_id)
        if result.get("valid") is True:
            validation_holder.clear()
            validation_holder.update(result)
        return _json_content(result)

    def execute(sql: str, validation_digest: str) -> str:
        nonlocal execution_attempted
        with execution_lock:
            if execution_attempted:
                return _json_content(
                    {
                        "version": 1,
                        "ok": False,
                        "error_code": "SQL_EXECUTION_ALREADY_ATTEMPTED",
                        "snapshot_id": snapshot_id,
                        "validation_digest": validation_holder.get("validation_digest"),
                    }
                )
            if validation_holder.get("valid") is True and validation_holder.get("sql_sha256") == sql_digest(sql) and validation_holder.get("validation_digest") == validation_digest:
                execution_attempted = True
        result = execute_sql(
            sql,
            validation_digest=validation_digest,
            validation=validation_holder,
            config=config,
            secrets=secrets,
        )
        result["snapshot_id"] = snapshot_id
        return _json_content(result)

    tools: list[BaseTool] = [
        StructuredTool.from_function(
            func=validate,
            name="data_validate_sql",
            description="按当前 DataAgent Evidence、数据库方言和 allowlist 校验单条只读 SQL。",
        )
    ]
    if action == "execute":
        # ADD: sql_only 快照在工具装配层移除执行入口，不能只依赖模型遵守 prompt。
        tools.append(
            StructuredTool.from_function(
                func=execute,
                name="data_execute_sql",
                description="执行最近一次 data_validate_sql 返回的 executable_sql。",
            )
        )
    return tools
