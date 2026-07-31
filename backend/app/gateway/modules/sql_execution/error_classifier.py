"""Gateway SQL Execution 数据库错误分类与脱敏。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_URI_CREDENTIAL_PATTERN = re.compile(
    r"\b([a-z][a-z0-9+.-]*://)([^@\s/]+)@",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ExecutionError:
    """数据库异常的安全结构化分类。

    Args:
        error_code: 稳定错误码。
        category: 数据库错误类别。
        retryable: SQL SubAgent 是否可尝试修复或重试。
        recommended_action: 推荐的下一步动作。
        message: 可选的安全数据库主错误信息。
    """

    error_code: str
    category: str
    retryable: bool
    recommended_action: str
    message: str | None = None


def _safe_database_message(
    exc: Exception,
    *,
    dsn: str,
    include: bool,
) -> str | None:
    """提取可供 SQL 修复使用的安全数据库主错误信息。

    Args:
        exc: 数据库驱动异常。
        dsn: 当前调用使用的 DSN，用于精确脱敏。
        include: 当前错误类型是否允许暴露主错误信息。

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


def classify_execution_error(
    exc: Exception,
    database_type: str,
    *,
    dsn: str,
) -> ExecutionError:
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

    if category == "execution_error" and isinstance(
        exc,
        (ConnectionError, TimeoutError),
    ):
        category = "connection_error"
        retryable = True
        recommended_action = "retry_same_sql"
    return ExecutionError(
        error_code=error_code,
        category=category,
        retryable=retryable,
        recommended_action=recommended_action,
        message=_safe_database_message(exc, dsn=dsn, include=True),
    )
