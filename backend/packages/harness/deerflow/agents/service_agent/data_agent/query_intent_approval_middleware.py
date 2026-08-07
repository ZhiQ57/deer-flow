"""DataAgent 查询意图审批工具 middleware。"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.agents.human_input import read_human_input_response

from .service_config import DataAgentServiceAbilityConfig


logger = logging.getLogger(__name__)

_TOOL_NAME = "ask_intent_approval"


class QueryIntentApprovalMiddleware(AgentMiddleware):
    """把 DataAgent 查询意图审批封装为模型原生工具调用协议。

    暂时该 middleware 只服务于 DataAgent.

    1. 模型调用 `ask_intent_approval` 时，服务端基于当前标签快照构造审批卡片并暂停；
    2. 人类提交 hidden ``human_input_response`` 后，服务端用同一个 ToolMessage ID
       替换审批请求，把最终审批结果写回工具消息历史.
    """

    def __init__(self, config: DataAgentServiceAbilityConfig) -> None:
        """初始化查询意图审批 middleware。

        Args:
            config: 当前 DataAgent service ability 配置。
        """
        super().__init__()
        self._config = config

    @override
    def before_agent(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """在人类提交审批回复后的下一次运行开始时投影工具结果。"""
        return self._detail_human_response(state)

    @override
    async def abefore_agent(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """异步运行复用同一审批回复投影逻辑。"""
        return self._detail_human_response(state)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步拦截 DataAgent 查询意图审批工具。"""
        if request.tool_call.get("name") != _TOOL_NAME:
            return handler(request)
        return self._handle_approval_request(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步拦截查询意图审批工具。"""
        if request.tool_call.get("name") != _TOOL_NAME:
            return await handler(request)
        return self._handle_approval_request(request)

    # ADD: 发送审批请求的主入口
    def _handle_approval_request(self, request: ToolCallRequest) -> ToolMessage | Command:
        """处理模型主动调用 `ask_intent_approval` 的请求"""
        # 获取全部请求参数
        args = request.tool_call.get("args")
        if not isinstance(args, Mapping):
            return self._error(request, "ask_intent_approval的输入参数不符合格式要求, 请检查重新调用/生成")

        # 获取详细参数值
        flow_id = args.get("flow_id")       # 关联意图标签
        if flow_id is None:
            return self._error(request, "ask_intent_approval的输入参数缺少 flow_id, 请检查重新调用/生成")

        asklist = args.get("asklist")       # 需要审批的问题和候选项
        if not isinstance(asklist, list) or not asklist:
            return self._error(request, "ask_intent_approval的输入参数 asklist 不符合格式要求, 请检查重新调用/生成")

        context = args.get("context")       # 需要审批的原因说明
        context = context.strip() if isinstance(context, str) and context.strip() else None

        tool_call_id = str(request.tool_call.get("id") or "missing-tool-call-id")

        try:
            approval_request = self._build_query_approval_request(
                flow_id=flow_id,
                context=context,
                asklist=asklist,
                tool_call_id=tool_call_id
            )
        except ValueError  as exc:
            return self._error(request, str(exc))

        approval = {
            "version": 1,
            "flow_id": flow_id,
            "status": "awaiting_confirmation",
            "action": None,
            "source": None,
            "request_id": approval_request["request_id"],
            "tool_call_id": tool_call_id,
        }

        artifact = {
            "version": 1,
            "kind": "data_query_intent_approval",
            "service_name": "data_query",
            "flow_id": flow_id,
            "human_input": approval_request,
            "approval": approval,
        }

        message = ToolMessage(
            id=str(approval_request["request_id"]),
            tool_call_id=tool_call_id,
            name=_TOOL_NAME,
            content=self._format_request_content(approval_request),
            artifact=artifact,
        )

        return Command(update={"messages": [message]}, goto=END)

    @staticmethod
    def _build_query_approval_request(
        flow_id: str,
        tool_call_id: str,
        asklist: list[Any],
        context: str | None
    ) -> dict[str, Any]:
        """根据 ask_intent_approval 工具参数构造 human_input_request"""

        digest = sha256(f"{flow_id}\n{tool_call_id}".encode("utf-8")).hexdigest()[:24]

        questions: list[dict[str, Any]] = []

        for question_index, item in enumerate(asklist, 1):
            if hasattr(item, "model_dump"):
                item = item.model_dump(exclude_none=True)
            if not isinstance(item, Mapping):
                raise ValueError("asklist 中的每一项都必须是对象。")

            question = str(item.get("question") or "").strip()
            if not question:
                raise ValueError("asklist.question 不能为空。")

            raw_options = item.get("options")
            options: list[dict[str, str]] = []

            if isinstance(raw_options, list):
                for option_index, option in enumerate(raw_options, 1):
                    label = str(option or "").strip()
                    if not label:
                        continue

                    option_id = f"question_{question_index}_option_{option_index}"
                    options.append(
                        {
                            "id": option_id,
                            "label": label,
                            "value": label,
                        }
                    )

            questions.append(
                {
                    "id": f"question_{question_index}",
                    "question": question,
                    "options": options,
                }
            )

        if not questions:
            raise ValueError("asklist 至少需要包含一个审批问题。")

        return {
            "version": 1,
            "kind": "human_input_request",
            "source": _TOOL_NAME,
            "request_id": f"{digest}",
            "flow_id": flow_id,
            "tool_call_id": tool_call_id,
            "title": "审批意图",
            "question": "请回答以下意图审批问题。",
            "context": context,
            "input_mode": "multi_question_choice",
            "questions": questions,
        }

    @staticmethod
    def _non_empty_string(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _human_response_summary(response: Mapping[str, Any]) -> str:
        """生成给模型和审计日志可读的回复摘要。"""
        if response.get("response_kind") == "option":
            value = str(response.get("value") or response.get("option_id") or "").strip()
            return value[:200]
        value = str(response.get("value") or "").strip()
        return value[:500]

    @staticmethod
    def _format_request_content(request_payload: Mapping[str, Any]) -> str:
        """构造审批请求 ToolMessage 的可读文本内容。"""
        question = str(request_payload.get("question") or "请确认当前查询意图。").strip()
        title = str(request_payload.get("title") or "确认数据库查询意图").strip()
        parts = [f"{title}\n\n{question}"]
        context = request_payload.get("context")
        if isinstance(context, str) and context.strip():
            parts.append(f"补充说明：{context.strip()}")
        options = request_payload.get("options")
        if isinstance(options, Sequence) and not isinstance(options, (str, bytes, bytearray)):
            labels = [str(item.get("label")).strip() for item in options if isinstance(item, Mapping) and isinstance(item.get("label"), str) and item.get("label").strip()]
            if labels:
                parts.append("可选操作：\n" + "\n".join(f"{index}. {label}" for index, label in enumerate(labels, 1)))
        return "\n\n".join(parts)


    @staticmethod
    def _error(request: ToolCallRequest, error: str) -> ToolMessage:
        """构造查询意图审批阶段错误。"""
        tool_call_id = str(request.tool_call.get("id") or "missing-tool-call-id")
        return ToolMessage(
            id=f"approval-error:{tool_call_id}",
            content=json.dumps({"ok": False, "error": error}, ensure_ascii=False),
            tool_call_id=tool_call_id,
            name=_TOOL_NAME,
            status="error",
        )


    # ADD: 处理人类审批回复
    def _detail_human_response(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        """把用户提交的审批回复转成最终 ToolMessage。"""

        # 读取最新的 hidden human-input 回复
        context = self._latest_pending_approval_context(state)
        if context is None:
            return None

        request_artifact, response = context
        request_payload = request_artifact.get("human_input")
        if not isinstance(request_payload, Mapping):
            return None

        if response.get("request_id") != request_payload.get("request_id"):
            return None
        if response.get("flow_id") != request_payload.get("flow_id"):
            return None

        # 解析多问题审批结果.
        review = self._parse_review_response(response, request_payload)
        if review is None:
            return None

        final_action = str(review["final_action"])
        if final_action == "cancel":
            status = "cancelled"
            action = "cancel"
        else:
            status = "approved"
            action = final_action

        result_artifact = self._build_approval_result_artifact(
            request_artifact=request_artifact,
            request_payload=request_payload,
            response=response,
            review=review,
            status=status,
            action=action,
        )
        approval_result = result_artifact["approval_result"]

        message = ToolMessage(
            id=str(
                request_payload.get("request_id")
                or f"data-query-approval:{request_payload.get('tool_call_id') or 'unknown'}"
            ),
            content=self._format_result_content(approval_result),
            tool_call_id=str(request_payload.get("tool_call_id") or "missing-tool-call-id"),
            name=_TOOL_NAME,
            artifact=result_artifact,
        )
        return {"messages": [message]}

    @classmethod
    def _latest_pending_approval_context(
        cls,
        state: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Mapping[str, Any]] | None:
        """从消息历史里找到最新的审批请求和对应的人类回复。"""
        messages = state.get("messages")
        if not isinstance(messages, Sequence):
            return None

        contexts: dict[tuple[str, str], dict[str, Any]] = {}

        for index, message in enumerate(messages):
            if isinstance(message, ToolMessage) and message.name == _TOOL_NAME:
                artifact = getattr(message, "artifact", None)

                if not isinstance(artifact, Mapping):
                    continue

                human_input = artifact["human_input"]
                if not isinstance(human_input, Mapping):
                    continue

                request_id = human_input.get("request_id")
                flow_id = human_input.get("flow_id")
                if not (
                    isinstance(request_id, str)
                    and request_id.strip()
                    and isinstance(flow_id, str)
                    and flow_id.strip()
                ):
                    continue

                key = (request_id, flow_id)
                entry = contexts.setdefault(
                    key,
                    {
                        "request_artifact": None,
                        "response": None,
                        "resolved": False,
                        "index": index,
                    },
                )
                entry["request_artifact"] = dict(artifact)
                entry["resolved"] = isinstance(artifact.get("approval_result"), Mapping)
                entry["index"] = index
                continue

            if not isinstance(message, HumanMessage):
                continue

            response = read_human_input_response(message.additional_kwargs)
            if response is None or response.get("source") != _TOOL_NAME:
                continue

            request_id = response.get("request_id")
            flow_id = response.get("flow_id")
            if not (
                isinstance(request_id, str)
                and request_id.strip()
                and isinstance(flow_id, str)
                and flow_id.strip()
            ):
                continue

            key = (request_id, flow_id)
            entry = contexts.get(key)
            if entry is not None:
                entry["response"] = response

        pending = [
            entry
            for entry in contexts.values()
            if entry["request_artifact"] is not None
            and entry["response"] is not None
            and entry["resolved"] is False
        ]
        if not pending:
            return None

        pending.sort(key=lambda item: item["index"])
        latest = pending[-1]
        return latest["request_artifact"], latest["response"]

    @staticmethod
    def _parse_review_response(
        response: Mapping[str, Any],
        request_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """解析前端逐项审核 JSON 回复。

        Args:
            response: hidden ``human_input_response``。
            request_payload:

        Returns:
            校验通过的逐项审核结果；普通文本修改或非法 JSON 返回 None。
        """
        if response.get("response_kind") != "text" or not isinstance(response.get("value"), str):
            return None
        try:
            raw = json.loads(response["value"])
        except (TypeError, json.JSONDecodeError):
            return None

        if not isinstance(raw, Mapping) or raw.get("kind") != "data_query_review_response":
            return None
        if raw.get("request_id") != request_payload.get("request_id"):
            return None
        if raw.get("flow_id") != request_payload.get("flow_id"):
            return None

        final_action = raw.get("final_action")
        if final_action not in {"execute", "sql_only", "cancel"}:
            return None

        answers_raw = raw.get("answers")
        answers: list[dict[str, Any]] = []
        if isinstance(answers_raw, list):
            for item in answers_raw:
                if not isinstance(item, Mapping):
                    continue
                question_id = str(item.get("question_id") or "").strip()
                option_id = str(item.get("option_id") or "").strip()
                value = item.get("value")
                if not question_id or not option_id or not isinstance(value, str):
                    continue
                answers.append(
                    {
                        "question_id": question_id,
                        "option_id": option_id,
                        "value": value.strip(),
                    }
                )

        return {
            "final_action": final_action,
            "answers": answers,
        }


    def _build_approval_result_artifact(
        self,
        *,
        request_artifact: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        response: Mapping[str, Any],
        review: Mapping[str, Any],
        status: str,
        action: str,
    ) -> dict[str, Any]:
        """把审批结果投影成前端和后续阶段都能读取的结构化 artifact。"""

        answers = review.get("answers") if isinstance(review.get("answers"), list) else []
        finish = {
            "flow_id": request_payload.get("flow_id"),
            "request_id": request_payload.get("request_id"),
            "status": status,
            "action": action,
            "answers": answers,
        }

        approval_result = {
            "version": 1,
            "kind": "ask_approval_result",
            "flow_id": request_payload.get("flow_id"),
            "request_id": request_payload.get("request_id"),
            "status": status,
            "action": action,
            "source": "human",
            "response_kind": response.get("response_kind"),
            "selected": self._human_response_summary(response),
            "answers": answers,
            "finish": finish,
            "approved_at": datetime.now(UTC).isoformat(),
        }

        artifact = dict(request_artifact)
        artifact["flow_id"] = request_payload.get("flow_id")
        artifact["human_input"] = dict(request_payload)
        artifact["approval"] = {
            "version": 1,
            "flow_id": request_payload.get("flow_id"),
            "status": status,
            "action": action,
            "source": "human",
            "request_id": request_payload.get("request_id"),
            "tool_call_id": request_payload.get("tool_call_id"),
        }
        artifact["approval_result"] = approval_result
        return artifact

    @staticmethod
    def _format_result_content(approval_result: Mapping[str, Any]) -> str:
        """构造给模型和界面看的审批结果文本。"""
        status = approval_result.get("status")
        action = approval_result.get("action")
        if status == "approved" and action == "execute":
            return "审批已通过, 批准自动执行SQL"
        if status == "approved" and action == "sql_only":
            return "审批已通过, 仅生成 SQL, 不允许自动执行SQL"
        if status == "cancelled":
            return "审批已取消, 请暂停执行, 告知用户已停止任务"
        return "审批结果已记录"
