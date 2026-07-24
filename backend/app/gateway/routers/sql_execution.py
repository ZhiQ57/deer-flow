"""DataAgent 前端手动 SQL 执行 API。"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.authz import require_permission
from deerflow.agents.service_agent.registry import DataAgentServiceAbility, resolve_service_ability_safely
from deerflow.agents.service_agent.sql_executor import (
    SqlExecutionRequest,
    SqlExecutionService,
    SqlValidationRequest,
)
from deerflow.config.agents_config import AGENT_NAME_PATTERN, load_agent_config

router = APIRouter(prefix="/api", tags=["sql-execution"])


class SqlExecuteRequest(BaseModel):
    """前端手动 SQL 执行请求。"""

    agent_name: str = Field(min_length=1, max_length=100, pattern=AGENT_NAME_PATTERN.pattern)
    sql: str = Field(min_length=1, max_length=50_000)
    source: Literal["manual_ui"] = "manual_ui"

    @field_validator("agent_name", "sql")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        """清理必填文本并拒绝纯空白内容。

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


async def _thread_owned_by(thread_store: Any, thread_id: str, user_id: str) -> bool:
    """检查 SQL 执行线程是否由当前用户明确拥有。

    Args:
        thread_store: Gateway 线程存储。
        thread_id: 当前线程标识。
        user_id: 当前认证用户标识。

    Returns:
        线程存在且所有者严格匹配时返回 True。
    """
    record = await thread_store.get(thread_id, user_id=user_id)
    return record is not None and record.get("user_id") == user_id


async def _load_sql_config(agent_name: str, user_id: str):
    """加载当前用户 DataAgent 的 SQL Executor 配置。

    Args:
        agent_name: Custom Agent 名称。
        user_id: 当前认证用户标识。

    Returns:
        已校验的 DataQueryServiceAbilityConfig。

    Raises:
        HTTPException: Agent 不存在、不是 DataAgent 或未启用 SQL Executor。
    """
    try:
        agent_config = await asyncio.to_thread(load_agent_config, agent_name, user_id=user_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found") from exc
    if agent_config is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
    ability = resolve_service_ability_safely(agent_config.service_ability)
    if not isinstance(ability, DataAgentServiceAbility):
        raise HTTPException(status_code=403, detail="Agent does not enable data_query SQL execution")
    config = ability.config
    if not config.sql_execution.enabled:
        raise HTTPException(status_code=403, detail="Agent does not enable data_query SQL execution")
    return config


def _validation_failure(error_code: str, database_type: str) -> SqlExecuteResponse:
    """把 SQL 校验失败转换为稳定的前端执行响应。

    Args:
        error_code: SQL Executor 校验错误码。
        database_type: 当前配置的数据库类型。

    Returns:
        不包含内部异常信息的失败响应。
    """
    return SqlExecuteResponse(
        ok=False,
        database_type=database_type,
        error_code=error_code,
        error_category="validation_error",
        retryable=False,
        recommended_action="edit_sql",
    )


@router.post(
    "/threads/{thread_id}/sql/execute",
    response_model=SqlExecuteResponse,
    summary="Execute DataAgent SQL",
    description="Validate and execute a read-only SQL code block against the current user's DataAgent database.",
)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def execute_sql(thread_id: str, body: SqlExecuteRequest, request: Request) -> SqlExecuteResponse:
    """校验并执行前端 SQL 代码块。

    Args:
        thread_id: 当前对话线程标识。
        body: 前端手动执行请求。
        request: FastAPI 请求上下文。

    Returns:
        JSON 安全的原始数据库结果或结构化 SQL 错误。

    Raises:
        HTTPException: 线程、Agent 或 SQL Executor 权限不满足。
    """
    user_id = str(request.state.auth.user.id)
    thread_store = getattr(request.app.state, "thread_store", None)
    if thread_store is None or not await _thread_owned_by(thread_store, thread_id, user_id):
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    config = await _load_sql_config(body.agent_name, user_id)
    service = SqlExecutionService(config)
    validation = service.validate(
        SqlValidationRequest(
            sql=body.sql,
            source=body.source,
        )
    )
    if validation.get("valid") is not True:
        return _validation_failure(
            str(validation.get("error_code") or "SQL_VALIDATION_FAILED"),
            config.sql_execution.database_type,
        )

    executable_sql = str(validation["executable_sql"])
    result = await service.aexecute(
        SqlExecutionRequest(
            sql=executable_sql,
            validation_digest=str(validation["validation_digest"]),
            validation=validation,
            source=body.source,
        )
    )
    return SqlExecuteResponse.model_validate(result)
