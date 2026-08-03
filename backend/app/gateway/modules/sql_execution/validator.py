"""Gateway SQL 只读语义校验器。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig

from .contracts import SqlValidationRequest, SqlValidationResult

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
_MYSQL_EXECUTABLE_COMMENT_PATTERN = re.compile(
    r"/\*!\s*\d{0,6}\s*",
    flags=re.IGNORECASE,
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


def sql_digest(sql: str) -> str:
    """计算规范化 SQL 或绑定文本的 SHA-256 摘要。

    Args:
        sql: 需要计算摘要的 SQL 或绑定文本。

    Returns:
        带 ``sha256:`` 前缀的摘要。
    """
    return f"sha256:{sha256(sql.encode('utf-8')).hexdigest()}"


def _is_allowed_schema(name: str, allowed_schemas: list[str]) -> bool:
    """按大小写不敏感方式匹配允许访问的 Schema。

    Args:
        name: SQL 使用的 Schema 名称。
        allowed_schemas: 服务端配置的 Schema 列表。

    Returns:
        Schema 是否允许访问。
    """
    if not allowed_schemas:
        return False
    lowered = name.lower()
    return any(item.lower() == lowered for item in allowed_schemas)


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


def validate_sql_request(
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
    if request.source not in {"subagent", "manual_ui"}:
        return {"version": 1, "valid": False, "error_code": "SQL_SOURCE_INVALID"}
    if not isinstance(sql, str) or not sql.strip():
        return {"version": 1, "valid": False, "error_code": "SQL_EMPTY"}
    if config.sql_execution.database_type == "mysql" and _MYSQL_EXECUTABLE_COMMENT_PATTERN.search(sql):
        return {
            "version": 1,
            "valid": False,
            "error_code": "SQL_EXECUTABLE_COMMENT_FORBIDDEN",
        }
    if not config.sql_execution.allowed_schemas:
        return {"version": 1, "valid": False, "error_code": "SQL_SCHEMA_REQUIRED"}
    legacy_retrieval = request.retrieval
    legacy_binding = legacy_retrieval.get("binding") if isinstance(legacy_retrieval, Mapping) else None
    binding = manual_binding if request.source == "manual_ui" else request.binding or legacy_binding
    if not isinstance(binding, Mapping) or not isinstance(
        binding.get("binding_fingerprint"),
        str,
    ):
        return {"version": 1, "valid": False, "error_code": "SQL_BINDING_REQUIRED"}
    if binding.get("data_source_id") != config.data_source_id or binding.get("database_type") != config.sql_execution.database_type:
        return {"version": 1, "valid": False, "error_code": "SQL_BINDING_MISMATCH"}
    try:
        statements = [item for item in parse(sql, read=_dialect(config)) if item is not None]
    except ParseError:
        return {"version": 1, "valid": False, "error_code": "SQL_PARSE_ERROR"}
    if len(statements) != 1:
        return {
            "version": 1,
            "valid": False,
            "error_code": "SQL_MULTIPLE_STATEMENTS",
        }
    parsed = statements[0]
    if not isinstance(parsed, exp.Expression):
        return {"version": 1, "valid": False, "error_code": "SQL_PARSE_ERROR"}
    if isinstance(parsed, _FORBIDDEN_SQL_NODES):
        return {
            "version": 1,
            "valid": False,
            "error_code": "SQL_READONLY_REQUIRED",
        }
    if not isinstance(parsed, (exp.Select, exp.Union)):
        return {"version": 1, "valid": False, "error_code": "SQL_SELECT_REQUIRED"}

    cte_names = {str(cte.alias_or_name).lower() for cte in parsed.find_all(exp.CTE)}
    physical_tables: list[exp.Table] = []
    for table in parsed.find_all(exp.Table):
        table_name = table.name.lower()
        if table_name in cte_names and not table.db:
            continue
        physical_tables.append(table)
    if not physical_tables:
        return {"version": 1, "valid": False, "error_code": "SQL_TABLE_REQUIRED"}

    for node in parsed.walk():
        if isinstance(
            node,
            (*_FORBIDDEN_SQL_NODES, exp.Lock, exp.Into, exp.Hint, exp.PropertyEQ),
        ):
            return {
                "version": 1,
                "valid": False,
                "error_code": "SQL_READONLY_REQUIRED",
            }
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            return {
                "version": 1,
                "valid": False,
                "error_code": "SQL_STAR_NOT_ALLOWED",
            }
        if isinstance(node, exp.SessionParameter):
            return {
                "version": 1,
                "valid": False,
                "error_code": "SQL_DANGEROUS_FUNCTION",
            }
        if isinstance(node, exp.Func):
            function_name = str(node.sql_name() or node.name).lower()
            if function_name in _DANGEROUS_FUNCTIONS:
                return {
                    "version": 1,
                    "valid": False,
                    "error_code": "SQL_DANGEROUS_FUNCTION",
                }
        if isinstance(node, exp.Anonymous) and str(node.name).lower() in _DANGEROUS_FUNCTIONS:
            return {
                "version": 1,
                "valid": False,
                "error_code": "SQL_DANGEROUS_FUNCTION",
            }
        if isinstance(node, exp.Table) and node in physical_tables:
            if node.catalog:
                return {
                    "version": 1,
                    "valid": False,
                    "error_code": "SQL_CROSS_DATABASE_FORBIDDEN",
                }
            schema_name = node.db or _default_schema(config)
            if config.sql_execution.database_type == "mysql" and schema_name.lower() in _MYSQL_SYSTEM_DATABASES:
                return {
                    "version": 1,
                    "valid": False,
                    "error_code": "SQL_SYSTEM_DATABASE_FORBIDDEN",
                }
            if not _is_allowed_schema(
                schema_name,
                config.sql_execution.allowed_schemas,
            ):
                return {
                    "version": 1,
                    "valid": False,
                    "error_code": "SQL_SCHEMA_NOT_ALLOWED",
                    "error_message": (f"Schema `{schema_name}` 未配置在 sql_execution.allowed_schemas 中。"),
                }

    executable = parsed.sql(dialect=_dialect(config), pretty=False).strip()
    existing_limit = parsed.args.get("limit")
    row_limit_applied = False
    if existing_limit is None:
        parsed = parsed.limit(config.sql_execution.max_rows)
        executable = parsed.sql(dialect=_dialect(config), pretty=False).strip()
        row_limit_applied = True
    elif isinstance(existing_limit, exp.Limit) and isinstance(existing_limit.expression, exp.Literal) and existing_limit.expression.is_number and int(existing_limit.expression.this) > config.sql_execution.max_rows:
        existing_limit.set(
            "expression",
            exp.Literal.number(config.sql_execution.max_rows),
        )
        executable = parsed.sql(dialect=_dialect(config), pretty=False).strip()
        row_limit_applied = True
    binding_fingerprint = str(binding["binding_fingerprint"])
    return {
        "version": 1,
        "valid": True,
        "executable_sql": executable,
        "sql_sha256": sql_digest(executable),
        "validation_digest": sql_digest(
            f"{config.data_source_id}\n{config.sql_execution.database_type}\n{binding_fingerprint}\n{request.source}\n{request.snapshot_id or ''}\n{executable}",
        ),
        "snapshot_id": request.snapshot_id,
        "database_type": config.sql_execution.database_type,
        "binding_fingerprint": binding_fingerprint,
        "row_limit_applied": row_limit_applied,
        "source": request.source,
    }
