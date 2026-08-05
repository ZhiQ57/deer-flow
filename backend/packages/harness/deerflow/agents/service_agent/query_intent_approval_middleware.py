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

from .config import DataQueryServiceAbilityConfig
from .state import (
    build_query_approval_request,
    build_query_review_items,
    get_active_service_state,
    make_service_state,
)

logger = logging.getLogger(__name__)

_TOOL_NAME = "ask_intent_approval"


class QueryIntentApprovalMiddleware(AgentMiddleware):
    """把 DataAgent 查询意图审批封装为模型原生工具调用协议。

    暂时该 middleware 只服务于 DataAgent.

    1. 模型调用 `ask_intent_approval` 时，服务端基于当前标签快照构造审批卡片并暂停；
    2. 人类提交 hidden ``human_input_response`` 后，服务端用同一个 ToolMessage ID
       替换审批请求，把最终审批结果写回工具消息历史.
    """

    def __init__(self, config: DataQueryServiceAbilityConfig) -> None:
        """初始化查询意图审批 middleware。

        Args:
            config: 当前 DataAgent service ability 配置。
        """
        super().__init__()
        self._config = config

    @override
    def before_agent(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """在人类提交审批回复后的下一次运行开始时投影工具结果。"""
        return self._project_response(state)

    @override
    async def abefore_agent(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """异步运行复用同一审批回复投影逻辑。"""
        return self._project_response(state)

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
        """异步拦截 DataAgent 查询意图审批工具。"""
        if request.tool_call.get("name") != _TOOL_NAME:
            return await handler(request)
        return self._handle_approval_request(request)

    # 主入口
    def _handle_approval_request(self, request: ToolCallRequest) -> ToolMessage | Command:
        """处理模型主动调用 ``ask_intent_approval`` 的请求。"""
        state = request.state if isinstance(request.state, Mapping) else {}
        active = get_active_service_state(state)
        if active is None:
            return self._error(request, "QUERY_INTENT_APPROVAL_SNAPSHOT_MISSING")

        payload = active.get("payload")
        if not isinstance(payload, Mapping):
            return self._error(request, "QUERY_INTENT_APPROVAL_PAYLOAD_INVALID")

        stage = active.get("stage")
        if stage == "awaiting_confirmation":
            pending_request = payload.get("approval_request")
            if isinstance(pending_request, Mapping):
                return self._hidden_status_message(
                    request,
                    content="当前 DataAgent 查询意图审批已在等待人类提交，请不要重复创建审批卡片。",
                )
            return self._error(request, "QUERY_INTENT_APPROVAL_REQUEST_MISSING")

        if stage in {"approved", "succeeded", "sql_ready"}:
            approval = payload.get("approval")
            action = approval.get("action") if isinstance(approval, Mapping) else None
            return self._hidden_status_message(
                request,
                content=f"当前查询意图已审批通过，审批动作为 {action or 'unknown'}，请直接继续后续 SQL 阶段。",
            )

        if stage == "cancelled":
            return self._hidden_status_message(
                request,
                content="当前查询意图审批已经取消；如果用户重新执行并仍需确认，请先重新发布标签快照，再调用 ask_intent_approval。",
            )

        if stage != "labels_published":
            return self._error(request, "QUERY_INTENT_APPROVAL_STAGE_NOT_READY")

        snapshot = payload.get("labels")
        if not isinstance(snapshot, Mapping):
            return self._error(request, "QUERY_INTENT_APPROVAL_LABELS_MISSING")
        tool_call_id = str(request.tool_call.get("id") or "missing-tool-call-id")
        try:
            approval_request = build_query_approval_request(snapshot, tool_call_id=tool_call_id)
            approval = {
                "version": 1,
                "snapshot_id": active.get("snapshot_id"),
                "status": "awaiting_confirmation",
                "action": None,
                "source": None,
                "request_id": approval_request["request_id"],
                "tool_call_id": tool_call_id,
            }
            message = self._request_message(
                active=active,
                payload=payload,
                request_payload=approval_request,
                approval=approval,
            )
        except ValueError:
            logger.debug("构造 DataAgent 查询意图审批请求失败。", exc_info=True)
            return self._error(request, "QUERY_INTENT_APPROVAL_REQUEST_INVALID")

        next_payload = {
            **dict(payload),
            "approval": approval,
            "approval_request": approval_request,
        }
        service_state = make_service_state(
            turn_id=str(active.get("turn_id") or ""),
            stage="awaiting_confirmation",
            snapshot_id=str(active.get("snapshot_id") or ""),
            data_source_id=self._config.data_source_id,
            payload=next_payload,
        )
        return Command(update={"messages": [message], "service_states": [service_state]}, goto=END)


    @staticmethod
    def _latest_response(state: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """读取最新 DataAgent 意图审批回复。

        Args:
            state: 当前 LangGraph checkpoint 状态。

        Returns:
            source 为 ``ask_intent_approval`` 的 hidden human-input 回复；没有时返回 None。
        """
        messages = state.get("messages")
        if not isinstance(messages, Sequence):
            return None
        for message in reversed(messages):
            if not isinstance(message, HumanMessage):
                continue
            response = read_human_input_response(message.additional_kwargs)
            if response is not None and response.get("source") == _TOOL_NAME:
                return response
        return None

    @staticmethod
    def _option_action(response: Mapping[str, Any]) -> str:
        """把按钮回复归一化为 DataAgent SQL 阶段动作。

        Args:
            response: ``human_input_response`` 结构化回复。

        Returns:
            ``execute``、``sql_only``、``cancel`` 或 ``modify``。
        """
        if response.get("response_kind") == "option":
            action = str(response.get("option_id") or response.get("value") or "").strip()
            if action in {"execute", "sql_only", "cancel"}:
                return action
        return "modify"

    @staticmethod
    def _parse_review_response(
        response: Mapping[str, Any],
        payload: Mapping[str, Any],
        snapshot_id: str,
    ) -> dict[str, Any] | None:
        """解析前端逐项审核 JSON 回复。

        Args:
            response: hidden ``human_input_response``。
            payload: 当前 DataAgent 服务状态 payload。
            snapshot_id: 当前标签快照 ID。

        Returns:
            校验通过的逐项审核结果；普通文本修改或非法 JSON 返回 None。
        """
        if response.get("response_kind") != "text" or not isinstance(response.get("value"), str):
            return None
        try:
            raw = json.loads(response["value"])
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(raw, Mapping) or raw.get("kind") != "data_query_review_response" or raw.get("snapshot_id") != snapshot_id:
            return None
        final_action = raw.get("final_action")
        if final_action not in {"execute", "sql_only", "cancel"}:
            return None
        expected_items = payload.get("review_items")
        if not isinstance(expected_items, list):
            return None
        if final_action == "cancel":
            return {"final_action": final_action, "items": []}

        submitted = raw.get("items")
        if not isinstance(submitted, list) or len(submitted) != len(expected_items):
            return None
        expected_ids = {item.get("id") for item in expected_items if isinstance(item, Mapping)}
        decisions: dict[str, dict[str, Any]] = {}
        for item in submitted:
            if not isinstance(item, Mapping):
                return None
            item_id = item.get("id")
            decision = item.get("decision")
            if not isinstance(item_id, str) or item_id not in expected_ids or item_id in decisions:
                return None
            if decision not in {"accept", "modify"}:
                return None
            value = item.get("value")
            if decision == "modify" and (not isinstance(value, str) or not value.strip() or len(value) > 500):
                return None
            decisions[item_id] = {
                "id": item_id,
                "decision": decision,
                "value": value.strip() if isinstance(value, str) else None,
            }
        if set(decisions) != expected_ids:
            return None
        return {"final_action": final_action, "items": list(decisions.values())}

    @staticmethod
    def _human_response_summary(response: Mapping[str, Any]) -> str:
        """生成给模型和审计日志可读的回复摘要。"""
        if response.get("response_kind") == "option":
            value = str(response.get("value") or response.get("option_id") or "").strip()
            return value[:200]
        value = str(response.get("value") or "").strip()
        return value[:500]

    @staticmethod
    def _revision_id(snapshot_id: str, revision_query: str) -> str:
        """生成用户修改意见对应的新检索快照 ID。"""
        revision = sha256(f"{snapshot_id}\n{revision_query}".encode()).hexdigest()[:24]
        return f"revision:sha256:{revision}"

    def _labels_artifact(
        self,
        active: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        human_input: Mapping[str, Any] | None = None,
        approval: Mapping[str, Any] | None = None,
        approval_result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """从服务状态重建查询意图卡片 artifact。

        Args:
            active: 当前 DataAgent service state。
            payload: 当前 service state payload。
            human_input: 可选的人类审批请求。
            approval: 可选的审批状态。
            approval_result: 可选的最终审批结果。

        Returns:
            前端 ``QueryIntentCard`` 可解析的 ``data_query_labels`` artifact。

        Raises:
            ValueError: 当前服务状态缺少标签快照。
        """
        snapshot = payload.get("labels")
        if not isinstance(snapshot, Mapping):
            raise ValueError("DataAgent 查询意图审批缺少标签快照。")
        snapshot_id = snapshot.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ValueError("DataAgent 查询意图审批缺少 snapshot_id。")

        approval_policy = payload.get("approval_policy")
        approval_policy = dict(approval_policy) if isinstance(approval_policy, Mapping) else None
        review_items = payload.get("review_items")
        if not isinstance(review_items, list):
            review_items = build_query_review_items(snapshot)

        artifact: dict[str, Any] = {
            "version": 1,
            "kind": "data_query_labels",
            "service_name": "data_query",
            "snapshot_id": snapshot_id,
            "data_source_id": self._config.data_source_id,
            "turn_id": str(active.get("turn_id") or snapshot.get("turn_id") or ""),
            "intent": str(snapshot.get("intent") or ""),
            "summary": snapshot.get("summary") if isinstance(snapshot.get("summary"), str) else None,
            "ambiguities": list(snapshot.get("ambiguities") or []) if isinstance(snapshot.get("ambiguities"), list) else [],
            "ambiguity_items": [dict(item) for item in review_items if isinstance(item, Mapping)],
            "labels": list(snapshot.get("labels") or []) if isinstance(snapshot.get("labels"), list) else [],
            "evidence": [],
            "retrieval_digest": str(snapshot.get("retrieval_digest") or snapshot.get("context_digest") or ""),
            "binding_fingerprint": str(snapshot.get("binding_fingerprint") or ""),
        }
        if approval_policy is not None:
            artifact["approval_required"] = bool(approval_policy.get("required"))
            artifact["approval_policy"] = approval_policy
        if approval is not None:
            artifact["approval"] = dict(approval)
        if human_input is not None:
            artifact["human_input"] = dict(human_input)
        if approval_result is not None:
            artifact["approval_result"] = dict(approval_result)
        return artifact

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
    def _format_result_content(result: Mapping[str, Any]) -> str:
        """构造审批结果 ToolMessage 的可读文本内容。"""
        action = result.get("action")
        if result.get("status") == "approved" and action == "execute":
            return "人类已确认当前查询意图：确认并执行。可以继续进入 SQL 生成与只读执行阶段。"
        if result.get("status") == "approved" and action == "sql_only":
            return "人类已确认当前查询意图：仅生成 SQL，不执行数据库查询。"
        if result.get("status") == "cancelled":
            return "人类已取消当前查询意图审批。请停止本次 DataAgent 查询流程。"
        if result.get("status") == "revision_requested":
            revision = str(result.get("revision_query") or "").strip()
            return f"人类要求修改当前查询意图：{revision}。请重新检索、重新发布标签，并再次根据需要请求审批。"
        return "人类审批结果已记录。"

    def _build_result_payload(
        self,
        *,
        active: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        response: Mapping[str, Any],
        status: str,
        action: str,
        revision_query: str | None = None,
        review: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造持久化和工具消息共用的审批结果。"""
        result: dict[str, Any] = {
            "version": 1,
            "kind": "data_query_intent_approval_result",
            "service_name": "data_query",
            "snapshot_id": active.get("snapshot_id"),
            "data_source_id": self._config.data_source_id,
            "request_id": request_payload.get("request_id"),
            "tool_call_id": request_payload.get("tool_call_id"),
            "status": status,
            "action": action,
            "source": "human",
            "response_kind": response.get("response_kind"),
            "selected": self._human_response_summary(response),
            "approved_at": datetime.now(UTC).isoformat(),
        }
        if revision_query:
            result["revision_query"] = revision_query
        if review is not None:
            result["review"] = dict(review)
        return result

    def _request_message(
        self,
        *,
        active: Mapping[str, Any],
        payload: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        approval: Mapping[str, Any],
    ) -> ToolMessage:
        """构造展示查询意图审批卡片的 ToolMessage。"""
        request_id = str(request_payload.get("request_id") or "")
        artifact = self._labels_artifact(active, payload, human_input=request_payload, approval=approval)
        return ToolMessage(
            id=request_id or f"data-query-approval:{request_payload.get('tool_call_id') or 'unknown'}",
            content=self._format_request_content(request_payload),
            tool_call_id=str(request_payload.get("tool_call_id") or "missing-tool-call-id"),
            name=_TOOL_NAME,
            artifact=artifact,
        )

    def _result_message(
        self,
        *,
        active: Mapping[str, Any],
        payload: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        approval: Mapping[str, Any],
        approval_result: Mapping[str, Any],
    ) -> ToolMessage:
        """构造替换审批请求的最终 ToolMessage。"""
        request_id = str(request_payload.get("request_id") or "")
        artifact = self._labels_artifact(
            active,
            payload,
            human_input=request_payload,
            approval=approval,
            approval_result=approval_result,
        )
        return ToolMessage(
            id=request_id or f"data-query-approval:{request_payload.get('tool_call_id') or 'unknown'}",
            content=self._format_result_content(approval_result),
            tool_call_id=str(request_payload.get("tool_call_id") or "missing-tool-call-id"),
            name=_TOOL_NAME,
            artifact=artifact,
        )

    @staticmethod
    def _hidden_status_message(request: ToolCallRequest, *, content: str) -> ToolMessage:
        """给重复或已完成的审批工具调用返回隐藏工具结果。"""
        tool_call_id = str(request.tool_call.get("id") or "missing-tool-call-id")
        digest = sha256(f"{tool_call_id}\n{content}".encode()).hexdigest()[:16]
        return ToolMessage(
            id=f"data-query-approval-status:{digest}",
            content=content,
            tool_call_id=tool_call_id,
            name=_TOOL_NAME,
            additional_kwargs={"hide_from_ui": True},
        )

    @staticmethod
    def _error(request: ToolCallRequest, code: str) -> ToolMessage:
        """构造查询意图审批阶段错误。"""
        tool_call_id = str(request.tool_call.get("id") or "missing-tool-call-id")
        return ToolMessage(
            id=f"data-query-approval-error:{tool_call_id}",
            content=json.dumps({"version": 1, "ok": False, "error_code": code}, ensure_ascii=False),
            tool_call_id=tool_call_id,
            name=_TOOL_NAME,
            status="error",
        )

    def _project_response(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        """把 hidden human-input 回复投影为最终工具结果和服务状态。"""
        active = get_active_service_state(state)
        if active is None or active.get("stage") != "awaiting_confirmation":
            return None
        payload = active.get("payload")
        if not isinstance(payload, Mapping):
            return None
        approval_request = payload.get("approval_request")
        if not isinstance(approval_request, Mapping):
            return None
        response = self._latest_response(state)
        if response is None or response.get("request_id") != approval_request.get("request_id"):
            return None

        snapshot_id = str(active.get("snapshot_id") or "")
        review = self._parse_review_response(response, payload, snapshot_id)

        if review is not None:
            final_action = str(review["final_action"])
            if final_action == "cancel":
                return self._state_for_terminal_response(
                    active=active,
                    payload=payload,
                    request_payload=approval_request,
                    response=response,
                    status="cancelled",
                    action="cancel",
                    review=review,
                )

            modifications = [
                str(item["value"]).strip()
                for item in review["items"]
                if item.get("decision") == "modify" and isinstance(item.get("value"), str) and str(item["value"]).strip()
            ]
            if modifications:
                return self._state_for_revision_response(
                    active=active,
                    payload=payload,
                    request_payload=approval_request,
                    response=response,
                    revision_query="；".join(modifications),
                    review=review,
                )

            return self._state_for_terminal_response(
                active=active,
                payload=payload,
                request_payload=approval_request,
                response=response,
                status="approved",
                action=final_action,
                review=review,
            )

        action = self._option_action(response)
        if action in {"execute", "sql_only"}:
            return self._state_for_terminal_response(
                active=active,
                payload=payload,
                request_payload=approval_request,
                response=response,
                status="approved",
                action=action,
            )
        if action == "cancel":
            return self._state_for_terminal_response(
                active=active,
                payload=payload,
                request_payload=approval_request,
                response=response,
                status="cancelled",
                action="cancel",
            )

        value = str(response.get("value") or "").strip()
        if not value:
            return None
        return self._state_for_revision_response(
            active=active,
            payload=payload,
            request_payload=approval_request,
            response=response,
            revision_query=value,
        )

    def _state_for_terminal_response(
        self,
        *,
        active: Mapping[str, Any],
        payload: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        response: Mapping[str, Any],
        status: str,
        action: str,
        review: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造审批通过或取消后的状态更新。"""
        approval_result = self._build_result_payload(
            active=active,
            request_payload=request_payload,
            response=response,
            status=status,
            action=action,
            review=review,
        )
        approval = {
            "version": 1,
            "snapshot_id": active.get("snapshot_id"),
            "status": status,
            "action": action,
            "source": "human",
            "request_id": request_payload.get("request_id"),
            "tool_call_id": request_payload.get("tool_call_id"),
        }
        next_payload = {
            **dict(payload),
            "approval": approval,
            "approval_result": approval_result,
        }
        if review is not None and status == "approved":
            next_payload["review_items"] = [
                {**dict(item), "status": "accepted"}
                for item in payload.get("review_items", [])
                if isinstance(item, Mapping)
            ]
        message = self._result_message(
            active=active,
            payload=next_payload,
            request_payload=request_payload,
            approval=approval,
            approval_result=approval_result,
        )
        service_state = make_service_state(
            turn_id=str(active.get("turn_id") or ""),
            stage="approved" if status == "approved" else "cancelled",
            snapshot_id=str(active.get("snapshot_id") or ""),
            data_source_id=self._config.data_source_id,
            payload=next_payload,
        )
        return {"messages": [message], "service_states": [service_state]}

    def _state_for_revision_response(
        self,
        *,
        active: Mapping[str, Any],
        payload: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        response: Mapping[str, Any],
        revision_query: str,
        review: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造人类修改查询意图后的重新检索状态。"""
        revision_query = revision_query.strip()
        approval_result = self._build_result_payload(
            active=active,
            request_payload=request_payload,
            response=response,
            status="revision_requested",
            action="modify",
            revision_query=revision_query,
            review=review,
        )
        approval = {
            "version": 1,
            "snapshot_id": active.get("snapshot_id"),
            "status": "revision_requested",
            "action": "modify",
            "source": "human",
            "request_id": request_payload.get("request_id"),
            "tool_call_id": request_payload.get("tool_call_id"),
        }
        message = self._result_message(
            active=active,
            payload={**dict(payload), "approval_result": approval_result},
            request_payload=request_payload,
            approval=approval,
            approval_result=approval_result,
        )
        service_state = make_service_state(
            turn_id=str(active.get("turn_id") or ""),
            stage="retrieving",
            snapshot_id=self._revision_id(str(active.get("snapshot_id") or ""), revision_query),
            data_source_id=self._config.data_source_id,
            payload={
                "revision_query": revision_query,
                "previous_snapshot_id": active.get("snapshot_id"),
                "approval_result": approval_result,
            },
        )
        return {"messages": [message], "service_states": [service_state]}
