"""Task tool for delegating work to subagents."""

import asyncio
import json
import logging
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, cast

from langchain.tools import InjectedToolCallId, tool
from langchain_core.callbacks import BaseCallbackManager
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.types import Command

from deerflow.authz.principal import normalize_authz_attributes
from deerflow.config import get_app_config
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.subagents.executor import (
    SubagentStatus,
    cleanup_background_task,
    get_background_task_result,
    request_cancel_background_task,
)
from deerflow.subagents.status_contract import (
    SubagentStatusValue,
    SubagentStopReasonValue,
    format_subagent_result_message,
    make_subagent_additional_kwargs,
)
from deerflow.subagents.tool_provider import (
    SubagentToolContext,
    build_registered_subagent_tools,
)
from deerflow.tools.types import Runtime
from deerflow.trace_context import DEERFLOW_TRACE_METADATA_KEY, get_current_trace_id, normalize_trace_id
from deerflow.utils.custom_events import aemit_custom_event

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

# Cache subagent token usage by tool_call_id so TokenUsageMiddleware can
# write it back to the triggering AIMessage's usage_metadata.
_subagent_usage_cache: dict[str, dict[str, int]] = {}


def _token_usage_cache_enabled(app_config: "AppConfig | None") -> bool:
    if app_config is None:
        try:
            app_config = get_app_config()
        except FileNotFoundError:
            return False
    return bool(getattr(getattr(app_config, "token_usage", None), "enabled", False))


def _cache_subagent_usage(tool_call_id: str, usage: dict | None, *, enabled: bool = True) -> None:
    if enabled and usage:
        _subagent_usage_cache[tool_call_id] = usage


def pop_cached_subagent_usage(tool_call_id: str) -> dict | None:
    return _subagent_usage_cache.pop(tool_call_id, None)


def _is_subagent_terminal(result: Any) -> bool:
    """Return whether a background subagent result is safe to clean up."""
    return result.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT} or getattr(result, "completed_at", None) is not None


async def _await_subagent_terminal(task_id: str, max_polls: int) -> Any | None:
    """Poll until the background subagent reaches a terminal status or we run out of polls."""
    for _ in range(max_polls):
        result = get_background_task_result(task_id)
        if result is None:
            return None
        if _is_subagent_terminal(result):
            return result
        await asyncio.sleep(5)
    return None


async def _deferred_cleanup_subagent_task(task_id: str, trace_id: str, max_polls: int) -> None:
    """Keep polling a cancelled subagent until it can be safely removed."""
    cleanup_poll_count = 0
    while True:
        result = get_background_task_result(task_id)
        if result is None:
            return
        if _is_subagent_terminal(result):
            cleanup_background_task(task_id)
            return
        if cleanup_poll_count >= max_polls:
            logger.warning(f"[trace={trace_id}] Deferred cleanup for task {task_id} timed out after {cleanup_poll_count} polls")
            return
        await asyncio.sleep(5)
        cleanup_poll_count += 1


def _log_cleanup_failure(cleanup_task: asyncio.Task[None], *, trace_id: str, task_id: str) -> None:
    if cleanup_task.cancelled():
        return

    exc = cleanup_task.exception()
    if exc is not None:
        logger.error(f"[trace={trace_id}] Deferred cleanup failed for task {task_id}: {exc}")


def _schedule_deferred_subagent_cleanup(task_id: str, trace_id: str, max_polls: int) -> None:
    logger.debug(f"[trace={trace_id}] Scheduling deferred cleanup for cancelled task {task_id}")
    cleanup_task = asyncio.create_task(_deferred_cleanup_subagent_task(task_id, trace_id, max_polls))
    cleanup_task.add_done_callback(lambda task: _log_cleanup_failure(task, trace_id=trace_id, task_id=task_id))


def _find_usage_recorder(runtime: Any) -> Any | None:
    """Find a callback handler with ``record_external_llm_usage_records`` in the runtime config.

    LangChain may pass ``config["callbacks"]`` in three different shapes:

    - ``None`` (no callbacks registered): no recorder.
    - A plain ``list[BaseCallbackHandler]``: iterate it directly.
    - A ``BaseCallbackManager`` instance (e.g. ``AsyncCallbackManager`` on async
      tool runs): managers are not iterable, so we unwrap ``.handlers`` first.

    Any other shape (e.g. a single handler object accidentally passed without a
    list wrapper) cannot be iterated safely; treat it as "no recorder" rather
    than raise.
    """
    if runtime is None:
        return None
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return None
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    if not callbacks:
        return None
    if not isinstance(callbacks, list):
        return None
    for cb in callbacks:
        if hasattr(cb, "record_external_llm_usage_records"):
            return cb
    return None


def _summarize_usage(records: list[dict] | None) -> dict | None:
    """Summarize token usage records into a compact dict for SSE events."""
    if not records:
        return None
    return {
        "input_tokens": sum(r.get("input_tokens", 0) or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) or 0 for r in records),
        "total_tokens": sum(r.get("total_tokens", 0) or 0 for r in records),
    }


def _report_subagent_usage(runtime: Any, result: Any) -> None:
    """Report subagent token usage to the parent RunJournal, if available.

    Each subagent task must be reported only once (guarded by usage_reported).
    """
    if getattr(result, "usage_reported", True):
        return
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    journal = _find_usage_recorder(runtime)
    if journal is None:
        logger.debug("No usage recorder found in runtime callbacks — subagent token usage not recorded")
        return
    try:
        journal.record_external_llm_usage_records(records)
        result.usage_reported = True
    except Exception:
        logger.warning("Failed to report subagent token usage", exc_info=True)


def _get_runtime_app_config(runtime: Any) -> "AppConfig | None":
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return cast("AppConfig", app_config)
    return None


def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    """Return the effective subagent skill allowlist under the parent policy."""
    if parent is None:
        return child
    if child is None:
        return list(parent)

    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]


# ADD: 从 SQL SubAgent 捕获的真实 ToolMessage 构造权威结果，禁止信任模型自由文本中的执行行。
def _build_data_query_sql_result_from_steps(
    steps: list[dict[str, Any]],
    *,
    active_state: dict[str, Any],
    data_source_id: str,
) -> dict[str, Any] | None:
    """从子代理工具步骤构造 DataAgent SQL 结果合同。

    Args:
        steps: SubagentExecutor 捕获的 AIMessage/ToolMessage 字典。
        active_state: 父线程当前 approved DataAgent 快照。
        data_source_id: 服务端配置的数据源标识。

    Returns:
        只包含真实校验/执行工具输出的 SQL 结果；合同不完整时返回 None。
    """
    snapshot_id = active_state.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        return None
    state_data_source_id = active_state.get("data_source_id")
    if state_data_source_id is not None and state_data_source_id != data_source_id:
        return None
    payload = active_state.get("payload")
    approval = payload.get("approval") if isinstance(payload, dict) else None
    retrieval = payload.get("retrieval") if isinstance(payload, dict) else None
    binding = retrieval.get("binding") if isinstance(retrieval, dict) else None
    expected_database_type = binding.get("database_type") if isinstance(binding, dict) else None
    expected_binding_fingerprint = binding.get("binding_fingerprint") if isinstance(binding, dict) else None
    if not isinstance(expected_database_type, str) or not isinstance(expected_binding_fingerprint, str):
        return None
    action = approval.get("action") if isinstance(approval, dict) and approval.get("status") == "approved" else None
    if action not in {"execute", "sql_only"}:
        return None

    parsed_tools: list[tuple[str, dict[str, Any]]] = []
    for step in steps:
        if not isinstance(step, dict) or step.get("type") != "tool":
            continue
        name = step.get("name")
        if not isinstance(name, str):
            continue
        artifact = step.get("artifact")
        if isinstance(artifact, dict):
            parsed_tools.append((name, artifact))

    validation: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    if action == "sql_only":
        if any(name == "data_execute_sql" for name, _value in parsed_tools):
            return None
        validate_payload = next((value for name, value in reversed(parsed_tools) if name == "data_validate_sql"), None)
        if isinstance(validate_payload, dict) and validate_payload.get("kind") == "data_query_sql_result":
            candidate = validate_payload.get("validation")
            validation = dict(candidate) if isinstance(candidate, dict) else None
            if validate_payload.get("execution") is not None:
                return None
    else:
        execute_payload = next((value for name, value in reversed(parsed_tools) if name == "data_execute_sql"), None)
        if isinstance(execute_payload, dict) and execute_payload.get("kind") == "data_query_sql_result":
            candidate_validation = execute_payload.get("validation")
            candidate_execution = execute_payload.get("execution")
            validation = dict(candidate_validation) if isinstance(candidate_validation, dict) else None
            execution = dict(candidate_execution) if isinstance(candidate_execution, dict) else None

    if (
        not isinstance(validation, dict)
        or validation.get("version") != 1
        or validation.get("valid") is not True
        or validation.get("snapshot_id") != snapshot_id
        or not isinstance(validation.get("executable_sql"), str)
        or not isinstance(validation.get("sql_sha256"), str)
        or not isinstance(validation.get("validation_digest"), str)
        or validation.get("database_type") != expected_database_type
        or validation.get("binding_fingerprint") != expected_binding_fingerprint
    ):
        return None

    if action == "execute":
        if (
            not isinstance(execution, dict)
            or execution.get("version") != 1
            or execution.get("snapshot_id") != snapshot_id
            or execution.get("validation_digest") != validation.get("validation_digest")
            or not isinstance(execution.get("ok"), bool)
        ):
            return None

    return {
        "version": 1,
        "kind": "data_query_sql_result",
        "snapshot_id": snapshot_id,
        "data_source_id": data_source_id,
        "validation": validation,
        "execution": execution,
    }


def _task_result_command(
    *,
    tool_call_id: str,
    status: SubagentStatusValue,
    result: str | None = None,
    error: str | None = None,
    stop_reason: SubagentStopReasonValue | None = None,
    model_name: str | None = None,
    usage: dict[str, int] | None = None,
    artifact: Any | None = None,
) -> Command:
    content, metadata_error = format_subagent_result_message(status, result=result, error=error, stop_reason=stop_reason)
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call_id,
                    name="task",
                    artifact=artifact,
                    additional_kwargs=make_subagent_additional_kwargs(
                        status,
                        result=result,
                        error=metadata_error,
                        stop_reason=stop_reason,
                        model_name=model_name,
                        token_usage=usage,
                    ),
                )
            ]
        }
    )


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> str | Command:
    """Delegate a task to a specialized subagent that runs in its own context.

    Subagents help you:
    - Preserve context by keeping exploration and implementation separate
    - Handle complex multi-step tasks autonomously
    - Execute commands or operations in isolated contexts

    Built-in subagent types:
    - **general-purpose**: A capable agent for complex, multi-step tasks that require
      both exploration and action. Use when the task requires complex reasoning,
      multiple dependent steps, or would benefit from isolated context.
    - **bash**: Command execution specialist for running bash commands. This is only
      available when host bash is explicitly allowed or when using an isolated shell
      sandbox such as `AioSandboxProvider`.

    Additional custom subagent types may be defined in config.yaml under
    `subagents.custom_agents`. Each custom type can have its own system prompt,
    tools, skills, model, and timeout configuration. If an unknown subagent_type
    is provided, the error message will list all available types.

    When to use this tool:
    - Complex tasks requiring multiple steps or tools
    - Tasks that produce verbose output
    - When you want to isolate context from the main conversation
    - Parallel research or exploration tasks

    When NOT to use this tool:
    - Simple, single-step operations (use tools directly)
    - Tasks requiring user interaction or clarification

    Args:
        description: A short (3-5 word) description of the task for logging/display. ALWAYS PROVIDE THIS PARAMETER FIRST.
        prompt: The task description for the subagent. Be specific and clear about what needs to be done. ALWAYS PROVIDE THIS PARAMETER SECOND.
        subagent_type: The type of subagent to use. ALWAYS PROVIDE THIS PARAMETER THIRD.
    """
    runtime_app_config = _get_runtime_app_config(runtime)
    cache_token_usage = _token_usage_cache_enabled(runtime_app_config)
    available_subagent_names = get_available_subagent_names(app_config=runtime_app_config) if runtime_app_config is not None else get_available_subagent_names()

    # Get subagent configuration
    config = get_subagent_config(subagent_type, app_config=runtime_app_config) if runtime_app_config is not None else get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(available_subagent_names)
        error = f"Unknown subagent type '{subagent_type}'. Available: {available}"
        return _task_result_command(
            tool_call_id=tool_call_id,
            status="failed",
            error=error,
        )
    if subagent_type == "bash":
        host_bash_allowed = is_host_bash_allowed(runtime_app_config) if runtime_app_config is not None else is_host_bash_allowed()
        if not host_bash_allowed:
            return _task_result_command(
                tool_call_id=tool_call_id,
                status="failed",
                error=LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE,
            )

    # Build config overrides
    overrides: dict = {}

    # Skills are loaded by SubagentExecutor per-session (aligned with Codex's pattern:
    # each subagent loads its own skills based on config, injected as conversation items).
    # No longer appended to system_prompt here.

    # Extract parent context from runtime
    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    trace_id = None
    user_id = None
    deerflow_trace_id = None
    metadata: dict = {}

    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")

        # Try to get parent model from configurable
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")

        # Get or generate trace_id for distributed tracing
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    # Get user_id for tracing (uses standard resolution order)
    user_id = resolve_runtime_user_id(runtime)

    # Propagate the authenticated runtime context so delegated tool calls are
    # evaluated by GuardrailMiddleware with the same identity/attribution as
    # the lead agent. Sourced from the server-side context written by
    # inject_authenticated_user_context (and run_id by the run worker); stays
    # None when absent (e.g. internal-auth runs) so guardrail behavior is
    # unchanged. Without this, role-aware policy silently mis-attributes any
    # tool call delegated to a subagent (user_role=None).
    parent_context = runtime.context if runtime is not None else None
    parent_context = parent_context if isinstance(parent_context, dict) else {}
    user_role = parent_context.get("user_role")
    oauth_provider = parent_context.get("oauth_provider")
    oauth_id = parent_context.get("oauth_id")
    run_id = parent_context.get("run_id")
    # IM-channel sender identity: group chats share one thread across senders,
    # so delegated bash commands need the dispatching turn's channel_user_id.
    channel_user_id = parent_context.get("channel_user_id")
    # Propagate authorization identity: is_internal (strict bool) and
    # authz_attributes (validated Mapping, copied). These follow the same
    # server-side provenance as user_role/oauth — see inject_authenticated_user_context.
    is_internal = parent_context.get("is_internal") is True
    authz_attributes = normalize_authz_attributes(parent_context.get("authz_attributes"))
    deerflow_trace_id = normalize_trace_id(parent_context.get(DEERFLOW_TRACE_METADATA_KEY)) or normalize_trace_id(metadata.get(DEERFLOW_TRACE_METADATA_KEY)) or get_current_trace_id()

    parent_available_skills = metadata.get("available_skills")
    if parent_available_skills is not None:
        overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)

    if overrides:
        config = replace(config, **overrides)

    # Get available tools (excluding task tool to prevent nesting)
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools

    # Inherit parent agent's tool_groups so subagents respect the same restrictions
    parent_tool_groups = metadata.get("tool_groups")
    resolved_app_config = runtime_app_config
    if config.model == "inherit" and parent_model is None and resolved_app_config is None:
        resolved_app_config = get_app_config()
    effective_model = resolve_subagent_model_name(config, parent_model, app_config=resolved_app_config)

    # Subagents should not have subagent tools enabled (prevent recursive nesting).
    # Subagents also must not get list_uploaded_files — they have an independent
    # ThreadState where runtime.state["uploaded_files"] is absent, so the
    # current-run file exclusion would not work.
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": False,
        "include_upload_tool": False,
    }
    if resolved_app_config is not None:
        available_tools_kwargs["app_config"] = resolved_app_config
    tools = get_available_tools(**available_tools_kwargs)

    data_query_active_state: dict[str, Any] | None = None
    data_query_runtime_ability: dict[str, Any] | None = None
    # ADD: 仅识别 DataAgent SQL 子任务的权威结果重建上下文，具体工具由应用层提供器注入。
    service_ability_raw = parent_context.get("data_query_service_ability")
    # ADD: SQL 结果重建必须同时满足服务端 custom-agent allowlist 判定，客户端开关或模型目标名都不能替代授权。
    if isinstance(service_ability_raw, dict) and parent_context.get("data_query_sql_subagent_allowed") is True:
        active_states = runtime.state.get("service_states") if runtime is not None and isinstance(runtime.state, dict) else None
        active_state = next(
            (item for item in reversed(active_states or []) if isinstance(item, dict) and item.get("service_name") == "data_query" and item.get("stage") == "approved"),
            None,
        )
        if (
            service_ability_raw.get("type") == "data_query"
            and service_ability_raw.get("version") == 1
            and service_ability_raw.get("enable_sql_rag") is True
            and service_ability_raw.get("sql_execution_enabled") is True
            and subagent_type == service_ability_raw.get("sql_subagent_name")
            and isinstance(service_ability_raw.get("data_source_id"), str)
            and active_state is not None
        ):
            data_query_active_state = active_state
            data_query_runtime_ability = service_ability_raw

    # ADD: Harness 只调用通用提供器扩展点；Gateway 等应用层决定是否注入业务工具。
    parent_state = runtime.state if runtime is not None and isinstance(runtime.state, dict) else {}
    tools.extend(
        build_registered_subagent_tools(
            SubagentToolContext(
                subagent_type=subagent_type,
                thread_id=str(thread_id) if thread_id is not None else None,
                run_id=str(run_id) if run_id is not None else None,
                user_id=str(user_id) if user_id is not None else None,
                agent_name=str(parent_context.get("agent_name")) if parent_context.get("agent_name") else None,
                parent_context=parent_context,
                parent_state=parent_state,
            )
        )
    )

    # Create executor
    executor_kwargs = {
        "config": config,
        "tools": tools,
        "parent_model": parent_model,
        "sandbox_state": sandbox_state,
        "thread_data": thread_data,
        "thread_id": thread_id,
        "trace_id": trace_id,
        "user_id": user_id,
        "user_role": user_role,
        "oauth_provider": oauth_provider,
        "oauth_id": oauth_id,
        "run_id": run_id,
        "channel_user_id": channel_user_id,
        "is_internal": is_internal,
        "authz_attributes": authz_attributes,
        "deerflow_trace_id": deerflow_trace_id,
    }
    if resolved_app_config is not None:
        executor_kwargs["app_config"] = resolved_app_config
    executor = SubagentExecutor(**executor_kwargs)

    # Start background execution (always async to prevent blocking)
    # Use tool_call_id as task_id for better traceability
    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    # Poll for task completion in backend (removes need for LLM to poll)
    poll_count = 0
    last_status = None
    last_message_count = 0  # Track how many AI messages we've already sent
    # Polling timeout: execution timeout + 60s buffer, checked every 5s
    max_poll_count = (config.timeout_seconds + 60) // 5

    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout={config.timeout_seconds}s, polling_limit={max_poll_count} polls)")

    writer = get_stream_writer()
    # Send Task Started message'
    await aemit_custom_event(
        {
            "type": "task_started",
            "task_id": task_id,
            "description": description,
            "model_name": effective_model,
        },
        writer=writer,
    )

    try:
        while True:
            result = get_background_task_result(task_id)

            if result is None:
                logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
                await aemit_custom_event(
                    {"type": "task_failed", "task_id": task_id, "error": "Task disappeared from background tasks"},
                    writer=writer,
                )
                cleanup_background_task(task_id)
                error = f"Task {task_id} disappeared from background tasks"
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="failed",
                    error=error,
                )

            # Log status changes for debugging
            if result.status != last_status:
                logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
                last_status = result.status

            # The collector publishes cumulative records. Reuse one snapshot for
            # both live progress and the terminal event so the frontend can
            # replace, rather than add, its per-task total.
            usage = _summarize_usage(getattr(result, "token_usage_records", None))

            # Check for new AI messages and send task_running events
            ai_messages = result.ai_messages or []
            current_message_count = len(ai_messages)
            if current_message_count > last_message_count:
                # Send task_running event for each new message
                for i in range(last_message_count, current_message_count):
                    message = ai_messages[i]
                    await aemit_custom_event(
                        {
                            "type": "task_running",
                            "task_id": task_id,
                            "message": message,
                            "message_index": i + 1,  # 1-based index for display
                            "total_messages": current_message_count,
                            "usage": usage,
                            "model_name": effective_model,
                        },
                        writer=writer,
                    )
                    logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
                last_message_count = current_message_count

            # Check if task completed, failed, or timed out
            if result.status == SubagentStatus.COMPLETED:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                task_result = result.result
                task_result_artifact: dict[str, Any] | None = None
                if data_query_active_state is not None and data_query_runtime_ability is not None:
                    # ADD: DataAgent SQL 结果只从捕获的工具输出重建，子代理最终自由文本不具备数据库事实权限。
                    authoritative_result = _build_data_query_sql_result_from_steps(
                        result.ai_messages or [],
                        active_state=data_query_active_state,
                        data_source_id=str(data_query_runtime_ability["data_source_id"]),
                    )
                    if authoritative_result is None:
                        error = "SQL_SUBAGENT_TOOL_RESULT_INVALID"
                        writer(
                            {
                                "type": "task_failed",
                                "task_id": task_id,
                                "error": error,
                                "usage": usage,
                                "model_name": effective_model,
                            }
                        )
                        cleanup_background_task(task_id)
                        return _task_result_command(
                            tool_call_id=tool_call_id,
                            status="failed",
                            error=error,
                            model_name=effective_model,
                            usage=usage,
                        )
                    task_result = json.dumps(authoritative_result, ensure_ascii=False, separators=(",", ":"))
                    task_result_artifact = authoritative_result
                await aemit_custom_event(
                    {
                        "type": "task_completed",
                        "task_id": task_id,
                        "result": task_result,
                        "usage": usage,
                        "model_name": effective_model,
                    },
                    writer=writer,
                )
                logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
                cleanup_background_task(task_id)
                # stop_reason carries a guardrail cap (token_capped / turn_capped)
                # when the run was ended early but still produced a final answer
                # — the work survives on result_brief like a clean success.
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="completed",
                    result=task_result,
                    stop_reason=result.stop_reason,
                    model_name=effective_model,
                    usage=usage,
                    artifact=task_result_artifact,
                )
            elif result.status == SubagentStatus.FAILED:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                await aemit_custom_event(
                    {
                        "type": "task_failed",
                        "task_id": task_id,
                        "error": result.error,
                        "usage": usage,
                        "model_name": effective_model,
                    },
                    writer=writer,
                )
                logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
                cleanup_background_task(task_id)
                # A turn-capped run with no usable output surfaces as failed +
                # stop_reason=turn_capped; the cap note lets the lead tell "out
                # of budget" from "broken subagent".
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="failed",
                    error=result.error,
                    stop_reason=result.stop_reason,
                    model_name=effective_model,
                    usage=usage,
                )
            elif result.status == SubagentStatus.CANCELLED:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                await aemit_custom_event(
                    {
                        "type": "task_cancelled",
                        "task_id": task_id,
                        "error": result.error,
                        "usage": usage,
                        "model_name": effective_model,
                    },
                    writer=writer,
                )
                logger.info(f"[trace={trace_id}] Task {task_id} cancelled: {result.error}")
                cleanup_background_task(task_id)
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="cancelled",
                    error=result.error,
                    model_name=effective_model,
                    usage=usage,
                )
            elif result.status == SubagentStatus.TIMED_OUT:
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                await aemit_custom_event(
                    {
                        "type": "task_timed_out",
                        "task_id": task_id,
                        "error": result.error,
                        "usage": usage,
                        "model_name": effective_model,
                    },
                    writer=writer,
                )
                logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
                cleanup_background_task(task_id)
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="timed_out",
                    error=result.error,
                    model_name=effective_model,
                    usage=usage,
                )

            # Still running, wait before next poll
            await asyncio.sleep(5)
            poll_count += 1

            # Polling timeout as a safety net (in case thread pool timeout doesn't work)
            # Set to execution timeout + 60s buffer, in 5s poll intervals
            # This catches edge cases where the background task gets stuck
            if poll_count > max_poll_count:
                timeout_minutes = config.timeout_seconds // 60
                logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
                _report_subagent_usage(runtime, result)
                usage = _summarize_usage(getattr(result, "token_usage_records", None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                await aemit_custom_event(
                    {
                        "type": "task_timed_out",
                        "task_id": task_id,
                        "usage": usage,
                        "model_name": effective_model,
                    },
                    writer=writer,
                )
                # The task may still be running in the background. Signal cooperative
                # cancellation and schedule deferred cleanup to remove the entry from
                # _background_tasks once the background thread reaches a terminal state.
                request_cancel_background_task(task_id)
                _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
                message = f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {result.status.value}"
                return _task_result_command(
                    tool_call_id=tool_call_id,
                    status="polling_timed_out",
                    error=message,
                    model_name=effective_model,
                    usage=usage,
                )
    except asyncio.CancelledError:
        # Signal the background subagent thread to stop cooperatively.
        request_cancel_background_task(task_id)

        # Wait (shielded) for the subagent to reach a terminal state so the
        # final token usage snapshot is reported to the parent RunJournal
        # before the parent worker persists get_completion_data().
        terminal_result = None
        try:
            terminal_result = await asyncio.shield(_await_subagent_terminal(task_id, max_poll_count))
        except asyncio.CancelledError:
            pass

        # Report whatever the subagent collected (even if we timed out).
        final_result = terminal_result or get_background_task_result(task_id)
        if final_result is not None:
            _report_subagent_usage(runtime, final_result)
        if final_result is not None and _is_subagent_terminal(final_result):
            cleanup_background_task(task_id)
        else:
            _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
    except Exception:
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
