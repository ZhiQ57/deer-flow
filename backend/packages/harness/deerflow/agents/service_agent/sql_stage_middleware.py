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

from deerflow.subagents.status_contract import make_subagent_additional_kwargs, read_subagent_result_metadata

from .config import DataQueryServiceAbilityConfig
from .state import get_active_service_state, make_service_state


class SqlStageMiddleware(AgentMiddleware):
    """限制 SQL 只从 approved 快照进入显式 sql-subagent。"""

    # ADD: 以解析后的 service ability 固定子代理名称，避免 agent 名称硬编码业务分支。
    def __init__(self, config: DataQueryServiceAbilityConfig) -> None:
        """初始化 SQL 阶段门禁。"""
        super().__init__()
        self._config = config

    @override
    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]) -> ToolMessage | Command:
        """同步阻断未授权 SQL 委派并校验返回合同。"""
        if not self._is_target(request):
            return handler(request)
        authorized = self._envelope(request)
        if authorized is None:
            return self._error(request, "SQL_STAGE_NOT_APPROVED")
        envelope, approval, active, binding = authorized
        return self._merge_result(request, handler(self._replace_task_args(request, envelope)), approval, active, binding)

    @override
    async def awrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]) -> ToolMessage | Command:
        """DataAgent 的 SQL 执行门控
        1. 执行SQL必须调用 SQL-Gateway 执行器工具, 才能执行SQL.
        2. 禁止 bash | general 代理执行SQL指令, 拦截后反馈给模型.(反馈必须使用 SQL-Gateway 执行器工具)
        """
        # 判断是否为 SQL SubAgent 请求, 不是则忽略处理
        if not self._is_target(request):
            return await handler(request)

        
        authorized = self._envelope(request)
        if authorized is None:
            return self._error(request, "SQL_STAGE_NOT_APPROVED")
        envelope, approval, active, binding = authorized
        return self._merge_result(request, await handler(self._replace_task_args(request, envelope)), approval, active, binding)


    def _is_target(self, request: ToolCallRequest) -> bool:
        """判断 task 是否请求当前配置的 SQL SubAgent。"""
        if request.tool_call.get("name") != "task":
            return False
        args = request.tool_call.get("args")
        return isinstance(args, Mapping) and args.get("subagent_type") == self._config.sql_subagent_name



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

    def _approval_from_payload(
        self,
        active: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """读取当前快照已确认授权，或按策略生成自动放行授权。

        Args:
            active: 当前 DataAgent service state。
            payload: 当前 service state payload。

        Returns:
            可以进入 SQL 阶段的审批对象；不满足门禁时返回 None。
        """
        approval = payload.get("approval")
        if active.get("stage") in {"approved", "sql_ready", "succeeded"} and isinstance(approval, Mapping) and approval.get("status") == "approved" and approval.get("action") in {"execute", "sql_only"}:
            return dict(approval)

        policy = payload.get("approval_policy")
        if active.get("stage") == "labels_published" and isinstance(policy, Mapping) and policy.get("required") is False:
            return {
                "version": 1,
                "snapshot_id": active.get("snapshot_id"),
                "status": "approved",
                "action": "execute",
                "source": "policy",
                "reason": policy.get("reason"),
            }
        return None
    
    # 入口
    def _envelope(self, request: ToolCallRequest) -> tuple[dict[str, Any], dict[str, Any], Mapping[str, Any], Mapping[str, Any]] | None:
        """从当前状态构造严格 JSON SQL SubAgent 请求与审批授权。"""
        state = request.state if isinstance(request.state, Mapping) else {}
        
        # 读取 DataAgent 活动状态
        active = get_active_service_state(state)
        if active is None:
            return None
        
        payload = active.get("payload")
        if not isinstance(payload, Mapping):
            return None
        approval = self._approval_from_payload(active, payload)
        if approval is None:
            return None
        labels = payload.get("labels")
        if not isinstance(labels, Mapping):
            return None
        runtime_context = getattr(request.runtime, "context", None)
        runtime_context = runtime_context if isinstance(runtime_context, Mapping) else {}
        # ADD: 正式 custom-agent 运行必须由服务端显式确认 SQL SubAgent 位于 allowable_subagents。
        if "data_query_service_ability" in runtime_context and runtime_context.get("data_query_sql_subagent_allowed") is not True:
            return None
        binding = runtime_context.get("data_query_binding")
        if (
            not isinstance(binding, Mapping)
            or binding.get("data_source_id") != self._config.data_source_id
            or binding.get("database_type") != self._config.sql_execution.database_type
            or not isinstance(binding.get("binding_fingerprint"), str)
        ):
            return None
        # ADD: 只传递当前标签和运行身份，不传 DSN、Secret 或完整数据库行。
        envelope = {
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
            "context_digest": labels.get("context_digest"),
            "instructions": (
                "基于父对话中已有的 MCP/SQLRAG 工具消息与当前标签生成只读 SQL。"
                "先调用 data_validate_sql。action=execute 时才可调用 data_execute_sql；"
                "执行失败且 retryable=true 时，根据 error_category、error_message 和 recommended_action 修复 SQL，"
                "重新调用 data_validate_sql 后再执行，不得超过 max_execution_attempts。"
                "最终只返回一个 JSON 对象，包含 version、kind、snapshot_id、data_source_id、validation、execution；"
                "validation 必须原样保留 sql_sha256 和 validation_digest；禁止 Markdown 和自由文本。"
            ),
        }
        return envelope, approval, active, binding

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

    @staticmethod
    def _sql_error_code(error: object) -> str:
        """从子任务失败元数据中提取安全 SQL 错误码。"""
        if isinstance(error, str):
            candidate = error.strip().split(maxsplit=1)[0].strip("。.,;:")
            if candidate.startswith("SQL_") and len(candidate) <= 100:
                return candidate
        return "SQL_SUBAGENT_FAILED"

    @staticmethod
    def _artifact_payload(message: ToolMessage) -> dict[str, Any] | None:
        """优先读取 ToolMessage artifact 中的 SQL 结果合同。"""
        artifact = getattr(message, "artifact", None)
        if isinstance(artifact, Mapping):
            return dict(artifact)
        additional_kwargs = message.additional_kwargs
        if isinstance(additional_kwargs, Mapping) and isinstance(additional_kwargs.get("artifact"), Mapping):
            return dict(additional_kwargs["artifact"])
        return None

    def _merge_result(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command,
        approval: Mapping[str, Any],
        active: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> ToolMessage | Command:
        """验证子代理返回并投影 sql_ready/failed 阶段。"""
        message = self._result_message(result)
        if message is None:
            return self._error(request, "SQL_SUBAGENT_RESULT_MISSING")
        subagent_result = read_subagent_result_metadata(message.additional_kwargs)
        if subagent_result is not None and subagent_result["status"] != "completed":
            # ADD: task_tool 自己已经知道 SQL 子任务失败原因时，父阶段直接透传真实 SQL_* 错误码，
            # 避免把上游工具结果无效、超时或执行失败二次误报为合同解析失败。
            return self._error(request, self._sql_error_code(subagent_result.get("error")))

        parsed = self._artifact_payload(message)
        if (
            not isinstance(parsed, Mapping)
            or parsed.get("version") != 1
            or parsed.get("kind") != "data_query_sql_result"
            or parsed.get("snapshot_id") != active.get("snapshot_id")
            or parsed.get("data_source_id") != self._config.data_source_id
        ):
            return self._error(request, "SQL_SUBAGENT_CONTRACT_INVALID")
        active_payload = active.get("payload") if isinstance(active.get("payload"), Mapping) else {}
        validation = parsed.get("validation")
        if (
            not isinstance(validation, Mapping)
            or validation.get("valid") is not True
            or validation.get("snapshot_id") != active.get("snapshot_id")
            or not isinstance(validation.get("validation_digest"), str)
            or not isinstance(validation.get("executable_sql"), str)
            or validation.get("database_type") != binding.get("database_type")
            or validation.get("binding_fingerprint") != binding.get("binding_fingerprint")
        ):
            return self._error(request, "SQL_VALIDATION_FAILED")
        action = approval.get("action")
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
            payload={**dict(active_payload), "approval": dict(approval), "sql_result": dict(parsed)},
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


