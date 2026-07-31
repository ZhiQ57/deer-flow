"""Gateway SQL Execution 请求与响应合同。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from deerflow.config.agents_config import AGENT_NAME_PATTERN

SqlExecutionSource = Literal["subagent", "manual_ui"]


class SqlValidationResult(TypedDict, total=False):
    """SQL 校验结果合同。"""

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
    """SQL Service 校验请求。

    Args:
        sql: 待校验 SQL。
        retrieval: 当前 Query Snapshot 的 TableRAG registry 和绑定。
        snapshot_id: 当前 Query Snapshot 标识。
        source: 可信调用来源。
    """

    sql: str
    retrieval: Mapping[str, Any] | None = None
    snapshot_id: str | None = None
    source: SqlExecutionSource = "subagent"


@dataclass(frozen=True, slots=True)
class SqlExecutionRequest:
    """SQL Service 执行请求。

    Args:
        sql: 校验后可执行 SQL。
        validation_digest: 当前校验摘要。
        validation: 当前校验结果。
        source: 可信调用来源。
    """

    sql: str
    validation_digest: str
    validation: Mapping[str, Any]
    source: SqlExecutionSource = "subagent"


class SqlExecuteRequest(BaseModel):
    """前端手动 SQL 执行请求。"""

    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1, max_length=100, pattern=AGENT_NAME_PATTERN.pattern)
    sql: str = Field(min_length=1, max_length=50_000)
    source: Literal["manual_ui"] = "manual_ui"

    @field_validator("agent_name", "sql")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        """清理必填文本。

        Args:
            value: 请求中的 Agent 名称或 SQL。

        Returns:
            去除首尾空白后的文本。

        Raises:
            ValueError: 文本只包含空白字符。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class SqlExecuteResponse(BaseModel):
    """前端 SQL 执行安全响应。"""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    ok: bool
    database_type: str
    error_code: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    retryable: bool | None = None
    recommended_action: str | None = None
    duration_ms: float = 0
    sql_sha256: str | None = None
    validation_digest: str | None = None
    snapshot_id: str | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    row_count: int | None = None
    returned_row_count: int | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    empty: bool = False


class InternalSqlRequest(BaseModel):
    """SQL SubAgent 内部请求。"""

    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1, max_length=100, pattern=AGENT_NAME_PATTERN.pattern)
    sql: str = Field(min_length=1, max_length=50_000)
    snapshot_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)

    @field_validator("agent_name", "sql", "snapshot_id", "run_id")
    @classmethod
    def _strip_internal_text(cls, value: str) -> str:
        """清理内部请求文本。

        Args:
            value: 内部请求字段值。

        Returns:
            去除首尾空白后的文本。

        Raises:
            ValueError: 字段为空。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class InternalSqlExecuteRequest(InternalSqlRequest):
    """SQL SubAgent 内部执行请求。"""

    validation_digest: str = Field(min_length=1, max_length=200)

    @field_validator("validation_digest")
    @classmethod
    def _strip_validation_digest(cls, value: str) -> str:
        """清理校验摘要。

        Args:
            value: ``data_validate_sql`` 返回的摘要。

        Returns:
            去除首尾空白后的摘要。

        Raises:
            ValueError: 摘要为空。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class InternalSqlResult(BaseModel):
    """SQL SubAgent 内部权威结果。"""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    kind: Literal["data_query_sql_result"] = "data_query_sql_result"
    snapshot_id: str
    data_source_id: str
    validation: dict[str, Any]
    execution: dict[str, Any] | None = None
