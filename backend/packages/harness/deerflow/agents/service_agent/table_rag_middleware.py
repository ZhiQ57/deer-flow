"""DataAgent TableRAG 检索结果登记 middleware。"""

# ADD: DataAgent 正式查询闭环新增，复用现有工具调用链登记只读 TableRAG 结果。
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.agents.human_input import read_human_input_response
from deerflow.runtime.secret_context import extract_request_secrets

from .binding import resolve_data_source_binding
from .config import DataQueryServiceAbilityConfig
from .sqlrag_contract import SQLRAG_RETRIEVE_TOOL_NAME, is_sqlrag_retrieval_tool_name
from .state import build_retrieval_context, get_active_service_state, make_service_state, merge_retrieval_contexts
from .tool_call_limits import keep_first_matching_tool_call


class TableRagStageMiddleware(AgentMiddleware):
    """把 TableRAG ToolMessage 投影到唯一的 service_states 活动快照。"""

    # ADD: 注入 DataAgent 数据源配置，模型和前端不能覆盖该绑定。
    def __init__(self, config: DataQueryServiceAbilityConfig) -> None:
        """初始化检索登记 middleware。

        Args:
            config: 当前 DataAgent service ability 配置。
        """
        super().__init__()
        self._config = config

    @staticmethod
    def _turn_id(state: Mapping[str, Any] | None, *, fallback: str) -> str:
        """读取当前可见用户消息 ID，隐藏确认回复不创建新业务轮次。"""
        messages = state.get("messages") if isinstance(state, Mapping) else None
        if isinstance(messages, Sequence):
            for message in reversed(messages):
                if not isinstance(message, HumanMessage):
                    continue
                if read_human_input_response(message.additional_kwargs) is not None:
                    continue
                if isinstance(message.id, str) and message.id:
                    return message.id
                digest = sha256(str(message.content).encode("utf-8")).hexdigest()[:24]
                return f"human:{digest}"
        return f"tool:{fallback or 'unknown'}"

    @staticmethod
    def _tool_message(result: ToolMessage | Command) -> ToolMessage | None:
        """从普通结果或 Command.update 中读取当前 ToolMessage。"""
        if isinstance(result, ToolMessage):
            return result
        update = result.update
        if not isinstance(update, Mapping):
            return None
        messages = update.get("messages")
        if not isinstance(messages, Sequence):
            return None
        return next((message for message in reversed(messages) if isinstance(message, ToolMessage)), None)

    @staticmethod
    def _json_payload(message: ToolMessage) -> Mapping[str, Any] | None:
        """解析 TableRAG 结构化 JSON 响应。"""
        content = message.content
        if isinstance(content, Mapping):
            return content
        if isinstance(content, str):
            try:
                payload = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return None
            return payload if isinstance(payload, Mapping) else None
        return None

    @staticmethod
    def _merge_update(result: ToolMessage | Command, service_state: Mapping[str, Any]) -> Command:
        """保留原工具结果并附加 service_states 更新。"""
        if isinstance(result, ToolMessage):
            return Command(update={"messages": [result], "service_states": [dict(service_state)]})
        if not isinstance(result.update, Mapping):
            return result
        update = dict(result.update)
        update["service_states"] = [dict(service_state)]
        return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)

    @staticmethod
    def _stage_error(request: ToolCallRequest, code: str) -> ToolMessage:
        """返回不泄露内部状态的检索阶段错误。"""
        return ToolMessage(
            content=json.dumps({"version": 1, "ok": False, "error_code": code}, ensure_ascii=False),
            tool_call_id=str(request.tool_call.get("id") or "missing-tool-call-id"),
            name=str(request.tool_call.get("name") or SQLRAG_RETRIEVE_TOOL_NAME),
            status="error",
        )

    def _project_result(self, request: ToolCallRequest, result: ToolMessage | Command) -> ToolMessage | Command:
        """将一次 TableRAG 结果转换为服务状态更新。"""
        tool_name = str(request.tool_call.get("name") or "")
        tool_call_id = str(request.tool_call.get("id") or "")
        turn_id = self._turn_id(request.state if isinstance(request.state, Mapping) else None, fallback=tool_call_id)
        message = self._tool_message(result)
        payload = self._json_payload(message) if message is not None else None
        active = get_active_service_state(request.state if isinstance(request.state, Mapping) else None)
        existing_payload = active.get("payload") if isinstance(active, Mapping) and isinstance(active.get("payload"), Mapping) else {}
        prior_calls = existing_payload.get("retrieval_calls")
        retrieval_calls = int(prior_calls) + 1 if isinstance(prior_calls, int) and not isinstance(prior_calls, bool) else 1

        # ADD: 每次检索只记录安全状态、摘要和时间，禁止把失败堆栈或连接信息写入 checkpoint。
        def _attempt(*, ok: bool, error_code: str | None = None, retrieval_digest: str | None = None) -> dict[str, Any]:
            attempt: dict[str, Any] = {
                "tool_name": tool_name,
                "ok": ok,
                "attempted_at": datetime.now(UTC).isoformat(),
            }
            if error_code is not None:
                attempt["error_code"] = error_code
            if retrieval_digest is not None:
                attempt["retrieval_digest"] = retrieval_digest
            return attempt

        # ADD: 同轮补充检索失败时保留最近成功 Evidence，只追加有界失败记录，避免空结果覆盖可用上下文。
        def _preserve_prior_success(error_code: str) -> ToolMessage | Command | None:
            existing_retrieval = existing_payload.get("retrieval")
            if not (isinstance(active, Mapping) and active.get("turn_id") == turn_id and isinstance(existing_retrieval, Mapping) and existing_retrieval.get("ok") is True):
                return None
            attempts = existing_payload.get("retrieval_attempts")
            attempts = [dict(item) for item in attempts if isinstance(item, Mapping)] if isinstance(attempts, Sequence) else []
            attempts = [*attempts, _attempt(ok=False, error_code=error_code)][-3:]
            next_payload = {
                **dict(existing_payload),
                "retrieval_calls": retrieval_calls,
                "retrieval_attempts": attempts,
                "last_retrieval_error": {
                    "error_code": error_code,
                    "tool_name": tool_name,
                },
            }
            return self._merge_update(
                result,
                make_service_state(
                    turn_id=turn_id,
                    stage=str(active.get("stage") or "retrieving"),
                    snapshot_id=str(active.get("snapshot_id") or existing_retrieval.get("retrieval_digest") or ""),
                    data_source_id=self._config.data_source_id,
                    payload=next_payload,
                ),
            )

        error_code = "TABLERAG_EMPTY_OR_FAILED"
        try:
            if payload is None:
                raise ValueError("TableRAG 未返回结构化 JSON。")
            try:
                runtime_context = getattr(request.runtime, "context", None)
                # ADD: 请求级 Secret 只用于服务端 fingerprint 解析，不写入 retrieval 或 checkpoint。
                binding = resolve_data_source_binding(
                    self._config,
                    secrets=extract_request_secrets(runtime_context),
                )
            except ValueError:
                error_code = "DATA_SOURCE_BINDING_INVALID"
                raise
            retrieval = build_retrieval_context(
                payload,
                tool_name=tool_name,
                turn_id=turn_id,
                data_source_id=self._config.data_source_id,
                binding=binding,
                request_args=request.tool_call.get("args"),
            )
        except ValueError:
            # ADD: 失败和空结果只写安全错误码，避免把连接细节或堆栈持久化到 checkpoint。
            preserved = _preserve_prior_success(error_code)
            if preserved is not None:
                return preserved
            retrieval = {
                "version": 1,
                "ok": False,
                "turn_id": turn_id,
                "data_source_id": self._config.data_source_id,
                "tool_name": tool_name,
                "error_code": error_code,
            }
            service_state = make_service_state(
                turn_id=turn_id,
                stage="needs_refinement",
                snapshot_id=f"needs-refinement:sha256:{sha256(f'{turn_id}\n{tool_name}'.encode()).hexdigest()}",
                data_source_id=self._config.data_source_id,
                payload={
                    "retrieval": retrieval,
                    "retrieval_calls": retrieval_calls,
                    "retrieval_attempts": [_attempt(ok=False, error_code=error_code)],
                },
            )
            return self._merge_update(result, service_state)

        # ADD: 补充检索合并当前轮次的 Evidence，同时清除旧 approval/SQL 授权，避免复用旧快照。
        if active and active.get("turn_id") == turn_id:
            existing_retrieval = existing_payload.get("retrieval")
            if isinstance(existing_retrieval, Mapping) and existing_retrieval.get("ok") is True:
                try:
                    retrieval = merge_retrieval_contexts(existing_retrieval, retrieval)
                except ValueError:
                    preserved = _preserve_prior_success("TABLERAG_SUPPLEMENT_MISMATCH")
                    if preserved is not None:
                        return preserved
                    raise
        attempts = existing_payload.get("retrieval_attempts")
        attempts = [dict(item) for item in attempts if isinstance(item, Mapping)] if isinstance(attempts, Sequence) else []
        attempts = [*attempts, _attempt(ok=True, retrieval_digest=str(retrieval["retrieval_digest"]))][-3:]
        next_payload = {
            **dict(existing_payload),
            "retrieval": retrieval,
            "retrieval_calls": retrieval_calls,
            "retrieval_attempts": attempts,
        }
        for stale_key in ("labels", "approval", "approval_request", "approval_error_code", "sql_result", "last_retrieval_error"):
            next_payload.pop(stale_key, None)
        service_state = make_service_state(
            turn_id=turn_id,
            stage="retrieving",
            snapshot_id=str(retrieval["retrieval_digest"]),
            data_source_id=self._config.data_source_id,
            payload=next_payload,
        )
        return self._merge_update(result, service_state)

    @override
    def after_model(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """同步模型返回后只保留一个 TableRAG 检索调用。"""
        return keep_first_matching_tool_call(state, lambda tool_call: is_sqlrag_retrieval_tool_name(tool_call.get("name")))

    @override
    async def aafter_model(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """异步模型返回后复用 TableRAG 串行化规则。"""
        return keep_first_matching_tool_call(state, lambda tool_call: is_sqlrag_retrieval_tool_name(tool_call.get("name")))

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步登记 TableRAG 结果，其他工具保持原流程。"""
        if not is_sqlrag_retrieval_tool_name(request.tool_call.get("name")):
            return handler(request)
        active = get_active_service_state(request.state if isinstance(request.state, Mapping) else None)
        turn_id = self._turn_id(request.state, fallback=str(request.tool_call.get("id") or ""))
        if isinstance(active, Mapping) and active.get("turn_id") == turn_id and active.get("stage") not in {"idle", "retrieving", "needs_refinement", "labels_published"}:
            return self._stage_error(request, "TABLERAG_STAGE_NOT_ALLOWED")
        active_payload = active.get("payload") if isinstance(active, Mapping) else None
        if isinstance(active_payload, Mapping) and active.get("turn_id") == turn_id and active_payload.get("retrieval_calls", 0) >= 3:
            return self._stage_error(request, "TABLERAG_RETRIEVAL_BUDGET")
        return self._project_result(request, handler(request))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步登记 TableRAG 结果，其他工具保持原流程。"""
        if not is_sqlrag_retrieval_tool_name(request.tool_call.get("name")):
            return await handler(request)
        active = get_active_service_state(request.state if isinstance(request.state, Mapping) else None)
        turn_id = self._turn_id(request.state, fallback=str(request.tool_call.get("id") or ""))
        if isinstance(active, Mapping) and active.get("turn_id") == turn_id and active.get("stage") not in {"idle", "retrieving", "needs_refinement", "labels_published"}:
            return self._stage_error(request, "TABLERAG_STAGE_NOT_ALLOWED")
        active_payload = active.get("payload") if isinstance(active, Mapping) else None
        if isinstance(active_payload, Mapping) and active.get("turn_id") == turn_id and active_payload.get("retrieval_calls", 0) >= 3:
            return self._stage_error(request, "TABLERAG_RETRIEVAL_BUDGET")
        return self._project_result(request, await handler(request))
