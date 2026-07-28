"""DataAgent SQL SubAgent 阶段门禁 middleware。"""

# ADD: DataAgent 正式查询闭环新增，父 lead-agent 只能通过 task 委派 SQL SubAgent。
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.subagents.status_contract import make_subagent_additional_kwargs

from .config import DataQueryServiceAbilityConfig
from .sql_executor import SqlExecutionService, SqlValidationRequest
from .state import get_active_service_state, make_service_state
from .tool_call_limits import keep_first_matching_tool_call
from .turn_reset_middleware import current_visible_turn_id


class SqlStageMiddleware(AgentMiddleware):
    """限制 SQL 只从 approved 快照进入显式 sql-subagent。"""

    # ADD: 以解析后的 service ability 固定子代理名称，避免 agent 名称硬编码业务分支。
    def __init__(self, config: DataQueryServiceAbilityConfig) -> None:
        """初始化 SQL 阶段门禁。"""
        super().__init__()
        self._config = config
        self._sql_executor = SqlExecutionService(config)

    @staticmethod
    def _error(request: ToolCallRequest, code: str) -> Command:
        """返回带子代理失败终态的阶段错误。"""
        tool_call_id = str(request.tool_call.get("id") or "missing-tool-call-id")
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps({"version": 1, "ok": False, "error_code": code}, ensure_ascii=False),
                        tool_call_id=tool_call_id,
                        name="task",
                        status="error",
                        additional_kwargs=make_subagent_additional_kwargs(
                            "failed",
                            error=code,
                        ),
                    )
                ]
            }
        )

    def _envelope(self, request: ToolCallRequest) -> dict[str, Any] | None:
        """从当前状态构造严格 JSON SQL SubAgent 请求。"""
        state = request.state if isinstance(request.state, Mapping) else {}
        if not self._config.enable_sql_rag:
            return None
        active = get_active_service_state(state)
        if active is None or active.get("stage") != "approved":
            return None
        visible_turn_id = current_visible_turn_id(state)
        if visible_turn_id is not None and visible_turn_id != active.get("turn_id"):
            return None
        payload = active.get("payload")
        if not isinstance(payload, Mapping):
            return None
        approval = payload.get("approval")
        if not isinstance(approval, Mapping) or approval.get("action") not in {"execute", "sql_only"}:
            return None
        retrieval = payload.get("retrieval")
        labels = payload.get("labels")
        if not isinstance(retrieval, Mapping) or not isinstance(labels, Mapping):
            return None
        runtime_context = getattr(request.runtime, "context", None)
        runtime_context = runtime_context if isinstance(runtime_context, Mapping) else {}
        # ADD: 正式 custom-agent 运行必须由服务端显式确认 SQL SubAgent 位于 allowable_subagents。
        if "data_query_service_ability" in runtime_context and runtime_context.get("data_query_sql_subagent_allowed") is not True:
            return None
        # ADD: 只传递当前 snapshot 的最小结构化证据，不传 DSN、Secret 或完整数据库行。
        return {
            "version": 1,
            "kind": "data_query_sql_request",
            "service_name": "data_query",
            "turn_id": active.get("turn_id"),
            "snapshot_id": active.get("snapshot_id"),
            "data_source_id": self._config.data_source_id,
            "ability_version": self._config.version,
            "thread_id": runtime_context.get("thread_id"),
            "parent_run_id": runtime_context.get("run_id"),
            "action": approval.get("action"),
            "database_type": self._config.sql_execution.database_type,
            "max_execution_attempts": self._config.sql_execution.max_execution_attempts,
            "intent": labels.get("intent"),
            "labels": labels.get("labels", []),
            "retrieval_digest": retrieval.get("retrieval_digest"),
            "evidence": retrieval.get("evidences", [])[:30] if isinstance(retrieval.get("evidences"), list) else [],
            "tables": retrieval.get("tables", [])[:30] if isinstance(retrieval.get("tables"), list) else [],
            "columns": retrieval.get("columns", [])[:100] if isinstance(retrieval.get("columns"), list) else [],
            "values": retrieval.get("values", [])[:50] if isinstance(retrieval.get("values"), list) else [],
            "join_graphs": retrieval.get("join_graphs", [])[:30] if isinstance(retrieval.get("join_graphs"), list) else [],
            "instructions": (
                "先调用 data_validate_sql。action=execute 时才可调用 data_execute_sql；"
                "执行失败且 retryable=true 时，根据 error_category、error_message 和 recommended_action 修复 SQL，"
                "重新调用 data_validate_sql 后再执行，不得超过 max_execution_attempts。"
                "最终只返回一个 JSON 对象，包含 version、kind、snapshot_id、data_source_id、validation、execution；"
                "validation 必须原样保留 sql_sha256 和 validation_digest；禁止 Markdown 和自由文本。"
            ),
        }

    @staticmethod
    def _replace_task_args(request: ToolCallRequest, envelope: Mapping[str, Any]) -> ToolCallRequest:
        """用 JSON envelope 替换 task prompt，保留现有 task 工具和路由。"""
        tool_call = dict(request.tool_call)
        args = dict(tool_call.get("args") or {})
        args["prompt"] = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        args["description"] = "生成并校验只读 SQL"
        tool_call["args"] = args
        return replace(request, tool_call=tool_call)

    @staticmethod
    def _result_message(result: ToolMessage | Command) -> ToolMessage | None:
        """读取 task 结果 ToolMessage。"""
        if isinstance(result, ToolMessage):
            return result
        if not isinstance(result.update, Mapping):
            return None
        messages = result.update.get("messages")
        if not isinstance(messages, list):
            return None
        return next((message for message in reversed(messages) if isinstance(message, ToolMessage)), None)

    def _merge_result(self, request: ToolCallRequest, result: ToolMessage | Command) -> ToolMessage | Command:
        """验证子代理返回并投影 sql_ready/failed 阶段。"""
        message = self._result_message(result)
        if message is None:
            return self._error(request, "SQL_SUBAGENT_RESULT_MISSING")
        state = request.state if isinstance(request.state, Mapping) else {}
        active = get_active_service_state(state)
        if active is None:
            return self._error(request, "SQL_SNAPSHOT_MISSING")
        content = message.content
        raw = content if isinstance(content, str) else ""
        if "Result:" in raw:
            raw = raw.split("Result:", 1)[1].strip()
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if (
            not isinstance(parsed, Mapping)
            or parsed.get("version") != 1
            or parsed.get("kind") != "data_query_sql_result"
            or parsed.get("snapshot_id") != active.get("snapshot_id")
            or parsed.get("data_source_id") != self._config.data_source_id
        ):
            return self._error(request, "SQL_SUBAGENT_CONTRACT_INVALID")
        validation = parsed.get("validation")
        if (
            not isinstance(validation, Mapping)
            or validation.get("valid") is not True
            or validation.get("snapshot_id") != active.get("snapshot_id")
            or not isinstance(validation.get("validation_digest"), str)
            or not isinstance(validation.get("executable_sql"), str)
        ):
            return self._error(request, "SQL_VALIDATION_FAILED")
        active_payload = active.get("payload") if isinstance(active.get("payload"), Mapping) else {}
        retrieval = active_payload.get("retrieval") if isinstance(active_payload.get("retrieval"), Mapping) else {}
        server_validation = self._sql_executor.validate(
            SqlValidationRequest(
                sql=validation["executable_sql"],
                retrieval=retrieval,
                snapshot_id=str(active.get("snapshot_id") or ""),
            )
        )
        if server_validation.get("valid") is not True or validation.get("sql_sha256") != server_validation.get("sql_sha256") or validation.get("validation_digest") != server_validation.get("validation_digest"):
            return self._error(request, "SQL_VALIDATION_FAILED")
        action = active.get("payload", {}).get("approval", {}).get("action") if isinstance(active.get("payload"), Mapping) else None
        execution = parsed.get("execution")
        if action == "execute":
            if isinstance(execution, Mapping) and execution.get("ok") is False:
                allowed_error_codes = {
                    "SQL_BINDING_MISMATCH",
                    "SQL_DSN_MISSING",
                    "SQL_EXECUTION_FAILED",
                    "SQL_TIMEOUT",
                    "SQL_CANCELLED",
                    "SQL_EXECUTION_ALREADY_ATTEMPTED",
                }
                if (
                    execution.get("version") != 1
                    or execution.get("snapshot_id") != active.get("snapshot_id")
                    or execution.get("validation_digest") != validation.get("validation_digest")
                    or execution.get("error_code") not in allowed_error_codes
                ):
                    return self._error(request, "SQL_EXECUTION_FAILED")
                stage = "failed"
            else:
                rows = execution.get("rows") if isinstance(execution, Mapping) else None
                if (
                    not isinstance(execution, Mapping)
                    or execution.get("version") != 1
                    or execution.get("ok") is not True
                    or execution.get("snapshot_id") != active.get("snapshot_id")
                    or execution.get("validation_digest") != validation.get("validation_digest")
                    or not isinstance(rows, list)
                    or len(rows) > self._config.sql_execution.max_rows
                ):
                    return self._error(request, "SQL_EXECUTION_FAILED")
                stage = "succeeded"
        else:
            if execution is not None:
                # ADD: sql_only 合同禁止携带任何执行结果，避免子代理自由文本伪造数据库行。
                return self._error(request, "SQL_SUBAGENT_CONTRACT_INVALID")
            stage = "succeeded"
        artifact = {
            "version": 1,
            "kind": "data_query_sql_result",
            "service_name": "data_query",
            "snapshot_id": active.get("snapshot_id"),
            "data_source_id": self._config.data_source_id,
            "validation": dict(validation),
            "execution": dict(execution) if isinstance(execution, Mapping) else None,
        }
        projected_message = message.model_copy(update={"artifact": artifact})
        service_state = make_service_state(
            turn_id=str(active.get("turn_id") or ""),
            stage=stage,
            snapshot_id=str(active.get("snapshot_id") or ""),
            data_source_id=self._config.data_source_id,
            payload={**dict(active_payload), "sql_result": dict(parsed)},
        )
        if isinstance(result, ToolMessage):
            return Command(update={"messages": [projected_message], "service_states": [service_state]})
        if not isinstance(result.update, Mapping):
            return result
        update = dict(result.update)
        messages = update.get("messages")
        if isinstance(messages, list):
            update["messages"] = [projected_message if item is message else item for item in messages]
        update["service_states"] = [service_state]
        return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)

    def _is_target(self, request: ToolCallRequest) -> bool:
        """判断 task 是否请求当前配置的 SQL SubAgent。"""
        if request.tool_call.get("name") != "task":
            return False
        args = request.tool_call.get("args")
        return isinstance(args, Mapping) and args.get("subagent_type") == self._config.sql_subagent_name

    # ADD: 单个模型响应只保留第一次 SQL SubAgent 委派，避免 LangGraph 并行 tool call 对同一快照重复执行。
    def _limit_parallel_sql_calls(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        """删除同一 AIMessage 中第二个及后续目标 SQL SubAgent 调用。"""
        return keep_first_matching_tool_call(
            state,
            lambda tool_call: tool_call.get("name") == "task" and isinstance(tool_call.get("args"), Mapping) and tool_call["args"].get("subagent_type") == self._config.sql_subagent_name,
        )

    @override
    def after_model(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """同步模型返回后限制并行 SQL SubAgent 调用。"""
        return self._limit_parallel_sql_calls(state)

    @override
    async def aafter_model(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """异步模型返回后复用同一并行 SQL SubAgent 限制。"""
        return self._limit_parallel_sql_calls(state)

    @override
    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]) -> ToolMessage | Command:
        """同步阻断未授权 SQL 委派并校验返回合同。"""
        if not self._is_target(request):
            return handler(request)
        envelope = self._envelope(request)
        if envelope is None:
            return self._error(request, "SQL_STAGE_NOT_APPROVED")
        return self._merge_result(request, handler(self._replace_task_args(request, envelope)))

    @override
    async def awrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]) -> ToolMessage | Command:
        """异步阻断未授权 SQL 委派并校验返回合同。"""
        if not self._is_target(request):
            return await handler(request)
        envelope = self._envelope(request)
        if envelope is None:
            return self._error(request, "SQL_STAGE_NOT_APPROVED")
        return self._merge_result(request, await handler(self._replace_task_args(request, envelope)))
