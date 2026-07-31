"""Gateway DataAgent SQL Execution API。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.gateway.auth_disabled import AUTH_SOURCE_INTERNAL
from app.gateway.authz import require_permission
from app.gateway.services import build_thread_checkpoint_state_accessor
from deerflow.agents.service_agent.registry import DataAgentServiceAbility, resolve_service_ability_safely
from deerflow.agents.service_agent.state import get_active_service_state
from deerflow.config.agents_config import load_agent_config

from .contracts import (
    InternalSqlExecuteRequest,
    InternalSqlRequest,
    InternalSqlResult,
    SqlExecuteRequest,
    SqlExecuteResponse,
    SqlExecutionRequest,
    SqlValidationRequest,
)
from .runtime_registry import SqlExecutionRunCapability, sql_execution_runtime_registry
from .service import SqlExecutionService

router = APIRouter(prefix="/api", tags=["sql-execution"])


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


def _validation_failure(validation: Mapping[str, Any], database_type: str) -> SqlExecuteResponse:
    """把 SQL 校验失败转换为稳定的前端执行响应。

    Args:
        validation: SQL Executor 返回的校验结果。
        database_type: 当前配置的数据库类型。

    Returns:
        包含直接校验原因但不包含内部异常信息的失败响应。
    """
    error_message = validation.get("error_message")
    return SqlExecuteResponse(
        ok=False,
        database_type=database_type,
        error_code=str(validation.get("error_code") or "SQL_VALIDATION_FAILED"),
        error_category="validation_error",
        error_message=error_message[:500] if isinstance(error_message, str) and error_message.strip() else None,
        retryable=False,
        recommended_action="edit_sql",
    )


@dataclass(frozen=True, slots=True)
class _InternalExecutionContext:
    """Gateway 内部 SQL 请求的权威上下文。

    Args:
        config: 当前 DataAgent 查询能力配置。
        active_state: 当前 approved Service State。
        retrieval: 当前 Snapshot 的 TableRAG 检索合同。
        capability: 当前 Run 的进程内 SQL 能力。
    """

    config: Any
    active_state: Mapping[str, Any]
    retrieval: Mapping[str, Any]
    capability: SqlExecutionRunCapability


async def _load_internal_execution_context(
    *,
    thread_id: str,
    body: InternalSqlRequest,
    request: Request,
    require_execute: bool,
) -> _InternalExecutionContext:
    """加载内部 SQL 请求的权威 Gateway 上下文。

    Args:
        thread_id: 当前线程标识。
        body: SQL SubAgent 内部请求。
        request: FastAPI 请求上下文。
        require_execute: 是否要求 Snapshot 已批准执行。

    Returns:
        已验证的内部 SQL 执行上下文。

    Raises:
        HTTPException: 身份、Run、Agent、Snapshot 或审批状态不匹配。
    """
    if getattr(request.state, "auth_source", None) != AUTH_SOURCE_INTERNAL:
        raise HTTPException(status_code=403, detail="Internal SQL execution requires trusted Gateway authentication")
    user_id = str(request.state.auth.user.id)
    capability = sql_execution_runtime_registry.resolve(
        run_id=body.run_id,
        thread_id=thread_id,
        user_id=user_id,
        agent_name=body.agent_name,
        snapshot_id=body.snapshot_id,
    )
    if capability is None:
        raise HTTPException(status_code=403, detail="SQL execution capability is missing or expired")

    config = await _load_sql_config(body.agent_name, user_id)
    accessor, state_config = await build_thread_checkpoint_state_accessor(
        request,
        thread_id=thread_id,
        fail_closed=True,
    )
    snapshot = await accessor.aget(state_config)
    values = snapshot.values if isinstance(snapshot.values, Mapping) else {}
    active = get_active_service_state(values)
    if not isinstance(active, Mapping) or active.get("snapshot_id") != body.snapshot_id:
        raise HTTPException(status_code=409, detail="DataAgent SQL snapshot is stale")
    payload = active.get("payload")
    approval = payload.get("approval") if isinstance(payload, Mapping) else None
    action = approval.get("action") if isinstance(approval, Mapping) and approval.get("status") == "approved" else None
    if action not in {"execute", "sql_only"} or (require_execute and action != "execute"):
        raise HTTPException(status_code=403, detail="DataAgent SQL snapshot is not approved for this operation")
    retrieval = payload.get("retrieval") if isinstance(payload, Mapping) else None
    binding = retrieval.get("binding") if isinstance(retrieval, Mapping) else None
    if not isinstance(retrieval, Mapping) or not isinstance(binding, Mapping) or binding.get("binding_fingerprint") != capability.binding.get("binding_fingerprint") or binding.get("data_source_id") != config.data_source_id:
        raise HTTPException(status_code=409, detail="DataAgent SQL binding is stale")
    return _InternalExecutionContext(
        config=config,
        active_state=active,
        retrieval=retrieval,
        capability=capability,
    )


def _internal_result(
    context: _InternalExecutionContext,
    validation: Mapping[str, Any],
    execution: Mapping[str, Any] | None = None,
) -> InternalSqlResult:
    """构造 SQL SubAgent 权威结果。

    Args:
        context: 当前内部执行上下文。
        validation: Gateway SQL 校验结果。
        execution: 可选 Gateway SQL 执行结果。

    Returns:
        可直接写入 ToolMessage artifact 的结果合同。
    """
    return InternalSqlResult(
        snapshot_id=str(context.active_state["snapshot_id"]),
        data_source_id=context.config.data_source_id,
        validation=dict(validation),
        execution=dict(execution) if isinstance(execution, Mapping) else None,
    )


def _attempt_rejected_execution(
    *,
    validation: Mapping[str, Any],
    database_type: str,
    attempt: int,
    max_attempts: int,
    category: str,
) -> dict[str, Any]:
    """构造执行预算或成功终态拒绝结果。

    Args:
        validation: 当前 SQL 校验结果。
        database_type: 当前数据库类型。
        attempt: 已占用执行次数。
        max_attempts: 最大执行次数。
        category: 拒绝类别。

    Returns:
        结构化 SQL 执行失败合同。
    """
    validation_error = category in {"validation_missing", "validation_mismatch"}
    requires_revalidation = category in {
        "validation_missing",
        "validation_mismatch",
        "validation_reuse",
    }
    return {
        "version": 1,
        "ok": False,
        "database_type": database_type,
        "error_code": ("SQL_DIGEST_MISMATCH" if validation_error else "SQL_EXECUTION_ALREADY_ATTEMPTED"),
        "error_category": category,
        "retryable": requires_revalidation,
        "recommended_action": ("revalidate_sql" if requires_revalidation else "stop"),
        "duration_ms": 0,
        "sql_sha256": validation.get("sql_sha256"),
        "validation_digest": validation.get("validation_digest"),
        "snapshot_id": validation.get("snapshot_id"),
        "attempt": attempt,
        "max_attempts": max_attempts,
    }


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
            validation,
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


@router.post(
    "/internal/threads/{thread_id}/sql/validate",
    response_model=InternalSqlResult,
    summary="Validate DataAgent SQL for a SQL SubAgent",
)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def validate_subagent_sql(
    thread_id: str,
    body: InternalSqlRequest,
    request: Request,
) -> InternalSqlResult:
    """通过 Gateway 权威 Snapshot 校验 SQL SubAgent 候选 SQL。

    Args:
        thread_id: 当前父线程标识。
        body: Gateway 工具注入的内部请求。
        request: FastAPI 请求上下文。

    Returns:
        带权威校验 artifact 的 DataAgent SQL 结果。

    Raises:
        HTTPException: 内部身份、Run、Snapshot 或绑定不匹配。
    """
    context = await _load_internal_execution_context(
        thread_id=thread_id,
        body=body,
        request=request,
        require_execute=False,
    )
    validation = SqlExecutionService(
        context.config,
        secrets=context.capability.secrets,
    ).validate(
        SqlValidationRequest(
            sql=body.sql,
            retrieval=context.retrieval,
            snapshot_id=body.snapshot_id,
            source="subagent",
        )
    )
    validation_digest = validation.get("validation_digest")
    if validation.get("valid") is True and isinstance(validation_digest, str):
        if not sql_execution_runtime_registry.record_validation(
            run_id=body.run_id,
            snapshot_id=body.snapshot_id,
            validation_digest=validation_digest,
        ):
            validation = {
                "version": 1,
                "valid": False,
                "error_code": "SQL_GATEWAY_CAPABILITY_EXPIRED",
                "snapshot_id": body.snapshot_id,
                "source": "subagent",
            }
    return _internal_result(context, validation)


@router.post(
    "/internal/threads/{thread_id}/sql/execute",
    response_model=InternalSqlResult,
    summary="Execute DataAgent SQL for a SQL SubAgent",
)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def execute_subagent_sql(
    thread_id: str,
    body: InternalSqlExecuteRequest,
    request: Request,
) -> InternalSqlResult:
    """通过 Gateway 权威 Snapshot 校验并执行 SQL SubAgent 候选 SQL。

    Args:
        thread_id: 当前父线程标识。
        body: Gateway 工具注入的内部请求。
        request: FastAPI 请求上下文。

    Returns:
        带权威校验和执行 artifact 的 DataAgent SQL 结果。

    Raises:
        HTTPException: 内部身份、Run、Snapshot、绑定或审批状态不匹配。
    """
    context = await _load_internal_execution_context(
        thread_id=thread_id,
        body=body,
        request=request,
        require_execute=True,
    )
    service = SqlExecutionService(
        context.config,
        secrets=context.capability.secrets,
    )
    validation = service.validate(
        SqlValidationRequest(
            sql=body.sql,
            retrieval=context.retrieval,
            snapshot_id=body.snapshot_id,
            source="subagent",
        )
    )
    if validation.get("valid") is not True:
        return _internal_result(context, validation)
    if validation.get("validation_digest") != body.validation_digest:
        execution = {
            "version": 1,
            "ok": False,
            "database_type": context.config.sql_execution.database_type,
            "error_code": "SQL_DIGEST_MISMATCH",
            "error_category": "validation_error",
            "retryable": True,
            "recommended_action": "revalidate_sql",
            "duration_ms": 0,
            "sql_sha256": validation.get("sql_sha256"),
            "validation_digest": validation.get("validation_digest"),
            "snapshot_id": body.snapshot_id,
            "attempt": 0,
            "max_attempts": context.config.sql_execution.max_execution_attempts,
        }
        return _internal_result(context, validation, execution)

    max_attempts = context.config.sql_execution.max_execution_attempts
    attempt, rejected_category = sql_execution_runtime_registry.reserve_attempt(
        run_id=body.run_id,
        snapshot_id=body.snapshot_id,
        validation_digest=body.validation_digest,
        max_attempts=max_attempts,
    )
    if rejected_category is not None:
        execution = _attempt_rejected_execution(
            validation=validation,
            database_type=context.config.sql_execution.database_type,
            attempt=attempt,
            max_attempts=max_attempts,
            category=rejected_category,
        )
        return _internal_result(context, validation, execution)

    executable_sql = str(validation["executable_sql"])
    execution = await service.aexecute(
        SqlExecutionRequest(
            sql=executable_sql,
            validation_digest=str(validation["validation_digest"]),
            validation=validation,
            source="subagent",
        )
    )
    execution["snapshot_id"] = body.snapshot_id
    execution["attempt"] = attempt
    execution["max_attempts"] = max_attempts
    if execution.get("ok") is True:
        sql_execution_runtime_registry.mark_succeeded(
            run_id=body.run_id,
            snapshot_id=body.snapshot_id,
        )
    return _internal_result(context, validation, execution)
