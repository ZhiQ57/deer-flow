"""DataAgent 阶段编排 middleware。"""

from __future__ import annotations

import asyncio
import json
import threading
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace as dc_replace
from hashlib import sha256
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from tools.constants import (
    DATA_BUILD_CHART_SPEC_TOOL_NAME,
    DATA_EXECUTE_SQL_TOOL_NAME,
    DATA_VALIDATE_SQL_TOOL_NAME,
    is_readonly_tablerag_tool_name,
    is_tablerag_retrieval_tool_name,
)
from tools.sql_validation import sql_sha256

from agents.middlewares._data_agent_messages import (
    insert_after_leading_system_messages,
    is_visible_user_message,
    message_content_text,
    result_messages,
)


class DataAgentOrchestrationMiddleware(AgentMiddleware):
    """DataAgent 阶段提示和工具调用门禁 middleware。"""

    def __init__(
        self,
        *,
        subagent_enabled: bool = False,
        allowed_subagents: set[str] | frozenset[str] = frozenset(),
        max_retrieval_calls: int = 6,
        max_sql_validation_calls: int = 4,
        max_sql_execution_calls: int = 2,
        max_chart_calls: int = 2,
    ) -> None:
        """初始化编排 middleware。

        Args:
            subagent_enabled: 是否启用原生 task 子代理工具。
            allowed_subagents: 允许委托的受限自定义子代理名称。
            max_retrieval_calls: 单轮最大 TableRAG 检索调用数。
            max_sql_validation_calls: 单轮最大 SQL 校验调用数。
            max_sql_execution_calls: 单轮最大 SQL 执行调用数。
            max_chart_calls: 单轮最大 ChartSpec 调用数。

        Return:
            None。
        """
        super().__init__()
        self._subagent_enabled = subagent_enabled
        self._allowed_subagents = frozenset(allowed_subagents)
        self._max_retrieval_calls = max_retrieval_calls
        self._max_sql_validation_calls = max_sql_validation_calls
        self._max_sql_execution_calls = max_sql_execution_calls
        self._max_chart_calls = max_chart_calls
        self._table_rag_locks: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
        self._table_rag_locks_guard = threading.Lock()

    def _message(self, state: Mapping[str, Any] | None = None) -> SystemMessage:
        """构造编排规则消息。

        Args:
            state: 当前 DataAgent 状态。

        Return:
            隐藏 SystemMessage。
        """
        resolved_state = state or {}
        stage = str(resolved_state.get("data_agent_stage") or "query_context")
        query_context = resolved_state.get("data_query_context")
        chart_requested = isinstance(query_context, Mapping) and query_context.get("intent") == "chart"
        chart_ready = isinstance(resolved_state.get("data_chart_spec"), Mapping)
        if chart_requested and self._execution_completed(resolved_state) and not chart_ready:
            next_action = f"用户已明确要求图表且 SQL 已成功执行；下一步必须调用 `{DATA_BUILD_CHART_SPEC_TOOL_NAME}`。在 ChartSpec 成功前不得输出最终答案，也不得继续检索、改写或重新校验 SQL。"
        elif chart_ready:
            next_action = "ChartSpec 已生成；停止调用数据工具并输出最终答案。"
        else:
            next_action = "继续完成当前阶段；只有用户明确要求图表或结果确实适合可视化时才进入 ChartSpec。"
        if self._subagent_enabled:
            allowed = "、".join(sorted(self._allowed_subagents))
            delegate_line = f"只允许使用 task 委托以下受限自定义子代理：{allowed}；不得调用其他子代理类型。"
        else:
            delegate_line = "当前没有 task 子代理工具；请在主代理内串行完成 TableRAG 检索、SQL 生成/校验和图表规格建议。"
        return SystemMessage(
            content=f"""<data_agent_orchestration>
按以下阶段执行 DataAgent 流程：
1. QueryContext：读取实体标签、标准术语、时间和排序信息。
2. TableRAG：使用名称包含 `tablerag` 的 MCP 工具检索 Evidence、表、列、字段值和 Join Graph；结果差时改写关键词继续检索。
3. NL2SQL：只基于确认过的 Evidence/表/列/Join 生成只读 SQL，并做语法、字段来源、聚合粒度、过滤条件和 LIMIT 检查。
4. ChartSpec：当用户要求图表或结果适合可视化时，给出图表类型、x/y/series 字段映射和排序/聚合说明。
5. FinalAnswer：最终答案必须呈现用户可读结论，并列出待确认项。
当前持久化阶段：`{stage}`。
阶段门禁：必须先成功调用 TableRAG，再调用 `{DATA_VALIDATE_SQL_TOOL_NAME}`；校验成功后把返回的 `executable_sql` 原样传给 `{DATA_EXECUTE_SQL_TOOL_NAME}`；执行成功后才能调用 `{DATA_BUILD_CHART_SPEC_TOOL_NAME}`。
当前动作约束：{next_action}
{delegate_line}
</data_agent_orchestration>""",
            additional_kwargs={"hide_from_ui": True, "data_agent_orchestration": True},
        )

    def _inject(self, request: ModelRequest) -> ModelRequest:
        """注入 DataAgent 编排规则。

        Args:
            request: 原始模型请求。

        Return:
            注入编排规则后的模型请求。
        """
        state = request.state if isinstance(request.state, Mapping) else None
        messages = insert_after_leading_system_messages(list(request.messages), [self._message(state)])
        return request.override(messages=messages)

    def _table_rag_lock(self, request: ToolCallRequest) -> threading.Lock:
        """获取当前线程作用域的 TableRAG 串行锁。

        DeerFlow 同步工具包装器会为并行 MCP 调用创建不同事件循环；同一线程并发
        初始化 stdio session 可能触发会话取消。DataAgent 在 thread_id 作用域内
        串行化 TableRAG 调用，同时保留不同会话之间的并发能力。

        Args:
            request: 工具调用请求。

        Return:
            当前 thread_id 对应的串行锁。
        """
        context = request.runtime.context if request.runtime is not None else None
        thread_id = context.get("thread_id") if isinstance(context, Mapping) else None
        key = str(thread_id or "default")
        with self._table_rag_locks_guard:
            lock = self._table_rag_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._table_rag_locks[key] = lock
            return lock

    @staticmethod
    async def _acquire_table_rag_lock(lock: threading.Lock) -> None:
        """以可取消方式获取跨线程 TableRAG 串行锁。

        `threading.Lock.acquire()` 不能直接在事件循环线程中阻塞；将永久等待放入
        `asyncio.to_thread()` 又会在协程取消时留下后台线程最终持锁却无人释放。
        因此仅执行非阻塞尝试，并在失败时短暂让出事件循环。

        Args:
            lock: 当前 thread_id 对应的线程锁。

        Return:
            获取锁后返回 None。
        """
        while not lock.acquire(blocking=False):
            await asyncio.sleep(0.01)

    @staticmethod
    def _block(request: ToolCallRequest, message: str) -> ToolMessage:
        """构造阶段门禁错误。

        Args:
            request: 工具调用请求。
            message: 给模型的修复提示。

        Return:
            错误 ToolMessage。
        """
        return ToolMessage(
            content=message,
            tool_call_id=str(request.tool_call.get("id") or "missing-tool-call-id"),
            name=str(request.tool_call.get("name") or "unknown-tool"),
            status="error",
            additional_kwargs={"data_agent_orchestration_blocked": True},
        )

    @staticmethod
    def _retrieval_completed(state: Mapping[str, Any]) -> bool:
        """判断 TableRAG 检索是否成功。

        Args:
            state: 当前图状态。

        Return:
            检索成功返回 True。
        """
        retrieval = state.get("data_retrieval_context")
        return isinstance(retrieval, Mapping) and retrieval.get("ok") is True

    @staticmethod
    def _retrieval_attempted(state: Mapping[str, Any]) -> bool:
        """判断本轮是否已经调用过 TableRAG 检索。

        Args:
            state: 当前图状态。

        Return:
            已存在本轮检索上下文时返回 True。
        """
        return isinstance(state.get("data_retrieval_context"), Mapping)

    @staticmethod
    def _validated_sql_matches(state: Mapping[str, Any], sql: str) -> bool:
        """判断待执行 SQL 是否与最近成功校验结果一致。

        Args:
            state: 当前图状态。
            sql: 待执行 SQL。

        Return:
            摘要匹配返回 True。
        """
        validation = state.get("data_sql_validation")
        if not isinstance(validation, Mapping) or validation.get("valid") is not True:
            return False
        return sql_sha256(sql) == validation.get("sql_sha256")

    @staticmethod
    def _execution_completed(state: Mapping[str, Any]) -> bool:
        """判断最近 SQL 校验与执行是否成功且属于同一条 SQL。

        Args:
            state: 当前图状态。

        Return:
            校验和执行摘要一致且执行成功时返回 True。
        """
        validation = state.get("data_sql_validation")
        execution = state.get("data_sql_execution")
        if not isinstance(validation, Mapping) or validation.get("valid") is not True:
            return False
        if not isinstance(execution, Mapping) or execution.get("ok") is not True:
            return False
        validation_digest = validation.get("sql_sha256")
        return bool(validation_digest) and validation_digest == execution.get("sql_sha256")

    @staticmethod
    def _tool_result_count(
        state: Mapping[str, Any],
        predicate: Callable[[str], bool],
    ) -> int:
        """统计当前轮次指定工具的结果消息数。

        QueryContextMiddleware 会在每轮开始时重置 DataAgent 阶段状态，但不会
        删除历史消息，因此只统计最后一条可见 HumanMessage 之后的 ToolMessage。

        Args:
            state: 当前图状态。
            predicate: 工具名匹配函数。

        Return:
            当前轮次匹配的工具结果数量。
        """
        count = 0
        for message in reversed(list(state.get("messages") or [])):
            if is_visible_user_message(message):
                break
            if isinstance(message, ToolMessage) and predicate(str(message.name or "")):
                count += 1
        return count

    def _gate_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        """按阶段阻止越序工具调用。

        Args:
            request: 工具调用请求。

        Return:
            应阻止时返回错误 ToolMessage，否则返回 None。
        """
        name = str(request.tool_call.get("name") or "")
        state = request.state if isinstance(request.state, Mapping) else {}
        execution_count = self._tool_result_count(state, lambda tool_name: tool_name == DATA_EXECUTE_SQL_TOOL_NAME)
        if is_tablerag_retrieval_tool_name(name):
            if execution_count >= self._max_sql_execution_calls:
                return self._block(request, "DataAgent 调用预算：本轮 SQL 执行次数已达上限，不再允许继续检索；请保留已有执行结果并输出结论或待确认项。")
            retrieval_count = self._tool_result_count(state, is_tablerag_retrieval_tool_name)
            if retrieval_count >= self._max_retrieval_calls:
                return self._block(request, "DataAgent 调用预算：本轮 TableRAG 检索次数已达上限，请基于已有证据回答或明确待确认项。")
        if name == "task":
            subagent_type = str((request.tool_call.get("args") or {}).get("subagent_type") or "")
            if not self._subagent_enabled or subagent_type not in self._allowed_subagents:
                allowed = "、".join(sorted(self._allowed_subagents)) or "无"
                return self._block(request, f"DataAgent 子代理门禁：仅允许受限自定义子代理：{allowed}。")
        if name == "ask_clarification" and not self._retrieval_attempted(state):
            return self._block(request, "DataAgent 阶段门禁：业务问题必须先调用 TableRAG 检索表、字段、字段值或口径；检索后仍不明确再追问用户。")
        if name == DATA_VALIDATE_SQL_TOOL_NAME and not self._retrieval_completed(state):
            return self._block(request, "DataAgent 阶段门禁：调用 data_validate_sql 前必须先成功调用只读 TableRAG 检索工具。")
        if name == DATA_VALIDATE_SQL_TOOL_NAME:
            if execution_count >= self._max_sql_execution_calls:
                return self._block(request, "DataAgent 调用预算：本轮 SQL 执行次数已达上限，不再允许校验新 SQL；请保留已有执行结果并输出结论或待确认项。")
            validation_count = self._tool_result_count(state, lambda tool_name: tool_name == DATA_VALIDATE_SQL_TOOL_NAME)
            if validation_count >= self._max_sql_validation_calls:
                return self._block(request, "DataAgent 调用预算：本轮 SQL 校验次数已达上限，请停止改写并说明当前缺口。")
        if name == DATA_EXECUTE_SQL_TOOL_NAME:
            if execution_count >= self._max_sql_execution_calls:
                return self._block(request, "DataAgent 调用预算：本轮 SQL 执行次数已达上限，请使用已有执行结果生成最终答案。")
            sql = str((request.tool_call.get("args") or {}).get("sql") or "")
            if not self._validated_sql_matches(state, sql):
                return self._block(request, "DataAgent 阶段门禁：请先调用 data_validate_sql，并把其返回的 executable_sql 原样传给 data_execute_sql。")
        if name == DATA_BUILD_CHART_SPEC_TOOL_NAME and not self._execution_completed(state):
            return self._block(request, "DataAgent 阶段门禁：只有 data_execute_sql 成功后才能生成 ChartSpec。")
        if name == DATA_BUILD_CHART_SPEC_TOOL_NAME:
            chart_count = self._tool_result_count(state, lambda tool_name: tool_name == DATA_BUILD_CHART_SPEC_TOOL_NAME)
            if chart_count >= self._max_chart_calls:
                return self._block(request, "DataAgent 调用预算：本轮 ChartSpec 生成次数已达上限，请直接使用已有查询结果回答。")
        return None

    @staticmethod
    def _retrieval_succeeded(
        tool_name: str,
        result: ToolMessage | Command,
    ) -> tuple[bool, str]:
        """判断 TableRAG 工具结果是否成功。

        Args:
            tool_name: TableRAG 工具名。
            result: 工具执行结果。

        Return:
            `(是否成功, 文本内容)`。
        """
        messages = result_messages(result)
        if not messages:
            return False, ""
        message = messages[-1]
        text = message_content_text(message.content)
        if getattr(message, "status", "success") == "error":
            return False, text
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return bool(text.strip()), text
        if isinstance(payload, Mapping) and payload.get("ok") is False:
            return False, text
        if not isinstance(payload, Mapping):
            return bool(payload), text

        retrieval_result = payload.get("result")
        normalized_name = tool_name.strip().lower()
        if normalized_name.endswith("tablerag_retrieve") or normalized_name.endswith("tablerag_raw_retrieve"):
            if not isinstance(retrieval_result, Mapping):
                return False, text
            candidate_keys = ("evidences", "tables", "columns", "values", "join_graphs")
            return any(bool(retrieval_result.get(key)) for key in candidate_keys), text
        return bool(retrieval_result), text

    @staticmethod
    def _attach_retrieval_update(
        request: ToolCallRequest,
        result: ToolMessage | Command,
    ) -> ToolMessage | Command:
        """把 TableRAG 检索摘要写入图状态。

        Args:
            request: 工具调用请求。
            result: 工具执行结果。

        Return:
            带检索状态更新的工具结果。
        """
        tool_name = str(request.tool_call.get("name") or "")
        ok, text = DataAgentOrchestrationMiddleware._retrieval_succeeded(tool_name, result)
        args = request.tool_call.get("args") or {}
        update = {
            "data_retrieval_context": {
                "ok": ok,
                "tool_name": tool_name,
                "query": str(args.get("query") or ""),
                "content_sha256": sha256(text.encode("utf-8")).hexdigest(),
                "result_preview": text[:2_000],
            },
            "data_generated_sql": None,
            "data_sql_validation": None,
            "data_sql_execution": None,
            "data_chart_spec": None,
        }
        if ok:
            update["data_agent_stage"] = "retrieval_completed"

        if isinstance(result, ToolMessage):
            return Command(update={**update, "messages": [result]})
        if isinstance(result.update, dict):
            return dc_replace(result, update={**result.update, **update})
        return result

    def _after_tool_call(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command,
    ) -> ToolMessage | Command:
        """处理工具结果并推进阶段状态。

        Args:
            request: 工具调用请求。
            result: 工具执行结果。

        Return:
            原结果或带状态更新的结果。
        """
        name = str(request.tool_call.get("name") or "")
        if is_tablerag_retrieval_tool_name(name):
            return self._attach_retrieval_update(request, result)
        return result

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._inject(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._inject(request))

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        blocked = self._gate_tool_call(request)
        if blocked is not None:
            return blocked
        name = str(request.tool_call.get("name") or "")
        if not is_readonly_tablerag_tool_name(name):
            return self._after_tool_call(request, handler(request))
        with self._table_rag_lock(request):
            return self._after_tool_call(request, handler(request))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        blocked = self._gate_tool_call(request)
        if blocked is not None:
            return blocked
        name = str(request.tool_call.get("name") or "")
        if not is_readonly_tablerag_tool_name(name):
            return self._after_tool_call(request, await handler(request))
        lock = self._table_rag_lock(request)
        await self._acquire_table_rag_lock(lock)
        try:
            return self._after_tool_call(request, await handler(request))
        finally:
            lock.release()
