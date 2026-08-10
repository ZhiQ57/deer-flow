"""Gateway Run 的 SQL Execution 上下文准备。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from deerflow.agents.service_agent.registry import DataAgentServiceAbility, resolve_service_ability_safely
from deerflow.config.agents_config import load_agent_config
from deerflow.runtime.secret_context import SECRETS_CONTEXT_KEY, extract_request_secrets

from .binding import resolve_data_source_binding
from .runtime_registry import sql_execution_runtime_registry


@dataclass(frozen=True, slots=True)
class PreparedSqlExecutionRunContext:
    """等待绑定到 Gateway Run 的 SQL Execution 上下文。

    Args:
        thread_id: 当前线程标识。
        user_id: 当前认证用户标识。
        agent_name: 当前 Custom Agent 名称。
        binding: 不含真实凭据的数据源绑定。
        secrets: 仅供 Gateway SQL 执行使用的 Secret 副本。
    """

    thread_id: str
    user_id: str
    agent_name: str
    binding: dict[str, Any]
    secrets: dict[str, str]


def _load_run_sql_binding(
    *,
    agent_name: str,
    user_id: str,
    secrets: dict[str, str],
) -> tuple[dict[str, Any], str] | None:
    """加载 Custom Agent 并解析无密钥绑定。

    Args:
        agent_name: 当前 Custom Agent 名称。
        user_id: 当前认证用户标识。
        secrets: 当前请求级 Secret。

    Returns:
        ``(binding, dsn_ref)``；非 DataAgent 时返回 None。
    """
    agent_config = load_agent_config(agent_name, user_id=user_id)
    if agent_config is None:
        return None
    ability = resolve_service_ability_safely(agent_config.service_ability)
    if not isinstance(ability, DataAgentServiceAbility) or not ability.config.sql_execution.enabled:
        return None
    binding = resolve_data_source_binding(ability.config, secrets=secrets)
    return binding, ability.config.sql_execution.dsn_env


def _detach_database_secret(
    context: dict[str, Any],
    dsn_ref: str,
    secrets: dict[str, str],
) -> dict[str, str]:
    """从 Harness 运行上下文移除 SQL 数据库 Secret。

    Args:
        context: 即将传入 Harness 的运行上下文。
        dsn_ref: Gateway 已解析的 SQL Execution DSN 引用。
        secrets: 请求级 Secret 的安全字符串副本。

    Returns:
        只包含 SQL Execution 所需 Secret 的 Gateway 注册表载荷。
    """
    if not dsn_ref.startswith("secret://"):
        return {}
    secret_name = dsn_ref.removeprefix("secret://")
    database_secret = secrets.get(secret_name)
    if not isinstance(database_secret, str):
        return {}

    raw_context_secrets = context.get(SECRETS_CONTEXT_KEY)
    if isinstance(raw_context_secrets, dict):
        remaining = dict(raw_context_secrets)
        remaining.pop(secret_name, None)
        if remaining:
            context[SECRETS_CONTEXT_KEY] = remaining
        else:
            context.pop(SECRETS_CONTEXT_KEY, None)
    return {secret_name: database_secret}


async def prepare_sql_execution_run_context(
    config: dict[str, Any],
) -> PreparedSqlExecutionRunContext | None:
    """为 Gateway DataAgent Run 解析无密钥绑定和运行时 Secret。

    Args:
        config: 即将传入 Harness 的 RunnableConfig 字典。

    Returns:
        等待绑定 Run 的 SQL 上下文；非 DataAgent 或绑定解析失败时返回 None。
    """
    context = config.get("context")
    if not isinstance(context, dict):
        return None
    agent_name = context.get("agent_name")
    user_id = context.get("user_id")
    thread_id = context.get("thread_id")
    if not all(isinstance(value, str) and value.strip() for value in (agent_name, user_id, thread_id)):
        return None
    secrets = extract_request_secrets(context)
    try:
        resolved = await asyncio.to_thread(
            _load_run_sql_binding,
            agent_name=agent_name,
            user_id=user_id,
            secrets=secrets,
        )
    except (FileNotFoundError, ValueError):
        context["data_query_binding_error"] = "DATA_SOURCE_BINDING_INVALID"
        return None
    if resolved is None:
        return None
    binding, dsn_ref = resolved
    database_secrets = _detach_database_secret(context, dsn_ref, secrets)
    context["data_query_binding"] = binding
    return PreparedSqlExecutionRunContext(
        thread_id=thread_id,
        user_id=user_id,
        agent_name=agent_name,
        binding=binding,
        secrets=database_secrets,
    )


def register_sql_execution_run_context(
    prepared: PreparedSqlExecutionRunContext | None,
    *,
    run_id: str,
) -> None:
    """将已准备的 SQL Execution 上下文同步绑定到持久化 Run。

    Args:
        prepared: Run 准入前完成解析的 SQL 上下文。
        run_id: Gateway 已创建的 Run 标识。

    Returns:
        无返回值；无需 SQL 能力时保持空操作。
    """
    if prepared is None:
        return
    sql_execution_runtime_registry.register_run(
        run_id=run_id,
        thread_id=prepared.thread_id,
        user_id=prepared.user_id,
        agent_name=prepared.agent_name,
        binding=prepared.binding,
        secrets=prepared.secrets,
    )


def release_sql_execution_run_context(run_id: str) -> None:
    """释放已结束 Gateway Run 的 SQL 能力和数据库 Secret。

    Args:
        run_id: 已完成、失败或取消的父运行标识。

    Returns:
        无返回值。
    """
    sql_execution_runtime_registry.discard_run(run_id)
