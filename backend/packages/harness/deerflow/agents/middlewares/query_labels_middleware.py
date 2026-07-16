"""用户意图标签的展示中间件。"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.agents.service_agent.state import (
    build_query_approval_request,
    build_query_label_snapshot,
    decide_query_approval,
    get_active_service_state,
    make_service_state,
)
from deerflow.agents.service_agent.tool_call_limits import keep_first_matching_tool_call
from deerflow.agents.service_agent.turn_reset_middleware import current_visible_turn_id

logger = logging.getLogger(__name__)

_PUBLISH_QUERY_LABELS_TOOL_NAME = "publish_query_labels"
_LABEL_SOURCES = frozenset(
    {
        "user",  # 用户直接声明的标签
        "database",  # 数据库检索结果映射的标签
        "derived",  # 模型推理生成的标签
    }
)


class QueryLabelsMiddleware(AgentMiddleware):
    """拦截标签声明工具并把结构化标签写入 runtime 状态。"""

    def __init__(
        self,
        *,
        require_retrieval: bool = False,
        stage_name: str | None = None,
        service_ability: object | None = None,
    ) -> None:
        """初始化查询标签 middleware。

        Args:
            require_retrieval: 是否要求所有标签都在首次有效 TableRAG 检索后发布。
            stage_name: 标签发布成功后写入的可选业务阶段名。
            service_ability: 当前 DataAgent service ability 配置，用于绑定数据源和合同版本。

        Return:
            None。
        """
        super().__init__()
        self._require_retrieval = require_retrieval
        self._stage_name = stage_name.strip() if isinstance(stage_name, str) and stage_name.strip() else None
        # ADD: 保存当前 service ability，后续标签快照必须绑定同一能力版本和数据源。
        self._service_ability = service_ability

    @staticmethod
    def _message_id(tool_call_id: str, payload: Mapping[str, Any] | str) -> str:
        """生成可重试覆盖的稳定 ToolMessage ID。

        Args:
            tool_call_id: 模型生成的工具调用 ID。
            payload: 标签快照或错误文本。

        Return:
            稳定消息 ID。
        """
        if tool_call_id:
            return f"query-labels:{tool_call_id}"
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) if isinstance(payload, Mapping) else payload
        digest = sha256(serialized.encode("utf-8")).hexdigest()[:16]
        return f"query-labels:{digest}"

    @staticmethod
    def _error(request: ToolCallRequest, message: str) -> ToolMessage:
        """构造标签参数错误消息。

        Args:
            request: 工具调用请求。
            message: 给 lead-agent 的修复提示。

        Return:
            不更新标签状态的错误 ToolMessage。
        """
        tool_call_id = str(request.tool_call.get("id") or "")
        return ToolMessage(
            id=QueryLabelsMiddleware._message_id(tool_call_id, message),
            content=json.dumps({"ok": False, "error": message}, ensure_ascii=False),
            tool_call_id=tool_call_id or "missing-tool-call-id",
            name=_PUBLISH_QUERY_LABELS_TOOL_NAME,
            status="error",
        )

    @staticmethod
    def _normalize_labels(raw_labels: Any) -> list[dict[str, str]]:
        """校验并规范化模型提交的标签数组。

        Args:
            raw_labels: 工具调用中的 labels 参数。

        Return:
            规范化后的标签列表。

        Raises:
            ValueError: 标签结构、数量、长度或来源不符合约束。
        """
        labels = raw_labels
        if isinstance(labels, str):
            try:
                labels = json.loads(labels)
            except json.JSONDecodeError as exc:
                raise ValueError("labels 必须是 JSON 数组。") from exc
        if not isinstance(labels, list) or not labels:
            raise ValueError("labels 必须是至少包含一项的数组。")
        if len(labels) > 30:
            raise ValueError("labels 不能超过 30 项。")

        normalized_labels: list[dict[str, str]] = []
        for index, item in enumerate(labels):
            if hasattr(item, "model_dump"):
                item = item.model_dump(exclude_none=True)
            if not isinstance(item, Mapping):
                raise ValueError(f"labels[{index}] 必须是对象。")

            label = item.get("label")
            value = item.get("value")
            source = item.get("source")
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f"labels[{index}].label 不能为空。")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"labels[{index}].value 不能为空。")
            if source not in _LABEL_SOURCES:
                raise ValueError(f"labels[{index}].source 必须是 user、database 或 derived。")

            record = {
                "label": label.strip(),
                "value": value.strip(),
                "source": str(source),
            }
            if len(record["label"]) > 50 or len(record["value"]) > 200:
                raise ValueError(f"labels[{index}] 的 label 或 value 超过长度限制。")
            for optional_name, maximum in (("normalized", 200), ("evidence", 500)):
                optional_value = item.get(optional_name)
                if optional_value is None:
                    continue
                if not isinstance(optional_value, str) or not optional_value.strip():
                    raise ValueError(f"labels[{index}].{optional_name} 必须是非空字符串。")
                cleaned = optional_value.strip()
                if len(cleaned) > maximum:
                    raise ValueError(f"labels[{index}].{optional_name} 超过长度限制。")
                record[optional_name] = cleaned
            if source == "database" and "evidence" not in record:
                raise ValueError(f"labels[{index}] 的数据库来源标签必须填写 evidence。")
            normalized_labels.append(record)
        return normalized_labels

    @staticmethod
    def _has_retrieval_evidence(request: ToolCallRequest) -> bool:
        """判断当前轮次是否已有成功 TableRAG 检索。

        Args:
            request: 工具调用请求。

        Return:
            已有成功检索状态时返回 True。
        """
        state = request.state if isinstance(request.state, Mapping) else {}
        retrieval = state.get("data_retrieval_context")
        return isinstance(retrieval, Mapping) and retrieval.get("ok") is True

    def _build_payload(self, request: ToolCallRequest) -> dict[str, Any]:
        """从工具参数构造顶层标签 artifact。

        Args:
            request: 工具调用请求。

        Return:
            可写入状态和 ToolMessage artifact 的标签快照。

        Raises:
            ValueError: 参数不符合标签合同。
        """
        args = request.tool_call.get("args") or {}
        if not isinstance(args, Mapping):
            raise ValueError("标签工具参数必须是对象。")

        intent = args.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent 不能为空。")
        normalized_intent = intent.strip()
        if len(normalized_intent) > 100:
            raise ValueError("intent 不能超过 100 个字符。")

        labels = self._normalize_labels(args.get("labels"))
        has_retrieval = self._has_retrieval_evidence(request)
        if self._require_retrieval and not has_retrieval:
            raise ValueError("查询标签只能在首次有效 TableRAG 检索完成后发布。")
        if any(item["source"] == "database" for item in labels) and not has_retrieval:
            raise ValueError("数据库来源标签只能在成功获得 TableRAG Evidence 后发布。")

        payload: dict[str, Any] = {
            "intent": normalized_intent,
            "labels": labels,
        }
        summary = args.get("summary")
        if summary is not None:
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("summary 必须是非空字符串。")
            normalized_summary = summary.strip()
            if len(normalized_summary) > 500:
                raise ValueError("summary 不能超过 500 个字符。")
            payload["summary"] = normalized_summary
        return payload

    # ADD: 依据当前 service_states 构造不可伪造的 DataAgent 标签快照。
    def _build_service_payload(self, request: ToolCallRequest) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """构造 DataAgent v1 标签 artifact、状态更新和是否需要确认标志。

        Args:
            request: 当前 publish_query_labels 工具请求。

        Returns:
            artifact、service_states 快照和是否等待确认。

        Raises:
            ValueError: 检索、标签或快照合同不合法。
        """
        if self._service_ability is None:
            raise ValueError("DataAgent service ability 未启用。")
        state = request.state if isinstance(request.state, Mapping) else {}
        active = get_active_service_state(state)
        if active is None:
            raise ValueError("发布查询标签前必须先完成当前用户轮次的 TableRAG 检索。")
        if active.get("stage") not in {"retrieving", "labels_published"}:
            raise ValueError("当前查询阶段不允许重复发布标签。")
        visible_turn_id = current_visible_turn_id(state)
        if visible_turn_id is not None and visible_turn_id != active.get("turn_id"):
            raise ValueError("查询标签不属于当前可见用户轮次。")
        payload = active.get("payload")
        retrieval = payload.get("retrieval") if isinstance(payload, Mapping) else None
        if not isinstance(retrieval, Mapping) or retrieval.get("ok") is not True:
            raise ValueError("当前 TableRAG 检索没有有效结果，不能发布数据库标签。")
        args = request.tool_call.get("args") or {}
        if not isinstance(args, Mapping):
            raise ValueError("标签工具参数必须是对象。")
        labels = args.get("labels")
        if isinstance(labels, str):
            try:
                labels = json.loads(labels)
            except json.JSONDecodeError as exc:
                raise ValueError("labels 必须是 JSON 数组。") from exc
        if not isinstance(labels, list):
            raise ValueError("labels 必须是数组。")
        normalized_labels: list[Mapping[str, Any]] = []
        for index, item in enumerate(labels):
            if hasattr(item, "model_dump"):
                item = item.model_dump(exclude_none=True)
            if not isinstance(item, Mapping):
                raise ValueError(f"labels[{index}] 必须是对象。")
            normalized_labels.append(dict(item))
        confidence = args.get("confidence")
        ambiguities = args.get("ambiguities") or []
        if isinstance(ambiguities, str):
            try:
                ambiguities = json.loads(ambiguities)
            except json.JSONDecodeError as exc:
                raise ValueError("ambiguities 必须是 JSON 数组。") from exc
        if not isinstance(ambiguities, list):
            raise ValueError("ambiguities 必须是数组。")
        turn_id = str(active.get("turn_id") or "")
        snapshot = build_query_label_snapshot(
            turn_id=turn_id,
            data_source_id=self._service_ability.data_source_id,
            ability_version=self._service_ability.version,
            retrieval=retrieval,
            intent=str(args.get("intent") or ""),
            labels=normalized_labels,
            summary=args.get("summary") if isinstance(args.get("summary"), str) else None,
            confidence=confidence,
            ambiguities=ambiguities,
        )
        approval = decide_query_approval(self._service_ability, snapshot)
        runtime_context = getattr(request.runtime, "context", None)
        confirmation_disabled = isinstance(runtime_context, Mapping) and bool(runtime_context.get("non_interactive") or runtime_context.get("disable_clarification"))
        approval_error_code: str | None = None
        if approval["status"] == "awaiting_confirmation" and confirmation_disabled:
            # ADD: 非交互/禁用澄清场景禁止等待确认或猜测执行数据库，直接安全取消当前快照。
            approval = {
                "version": 1,
                "snapshot_id": snapshot["snapshot_id"],
                "status": "cancelled",
                "action": "cancel",
                "source": "model",
            }
            approval_error_code = "DATA_QUERY_CONFIRMATION_UNAVAILABLE"
        tool_call_id = str(request.tool_call.get("id") or "")
        # ADD: 只把有限长度的 Evidence 摘要投影给前端，原始检索对象仍留在受控服务状态。
        evidence: list[dict[str, str]] = []
        registry = retrieval.get("registry") if isinstance(retrieval.get("registry"), Mapping) else {}
        for ref, item in list(registry.items())[:30]:
            if not isinstance(ref, str) or not isinstance(item, Mapping):
                continue
            record = item.get("record") if isinstance(item.get("record"), Mapping) else {}
            summary = next(
                (str(record.get(name)).strip() for name in ("evidence_content", "content", "value", "column_name", "table_name") if isinstance(record.get(name), str) and str(record.get(name)).strip()),
                "检索对象",
            )
            evidence.append({"ref": ref, "kind": str(item.get("kind") or "evidence"), "summary": summary[:500]})
        artifact: dict[str, Any] = {
            "version": 1,
            "kind": "data_query_labels",
            "service_name": "data_query",
            "snapshot_id": snapshot["snapshot_id"],
            "data_source_id": self._service_ability.data_source_id,
            "turn_id": turn_id,
            "intent": snapshot["intent"],
            "summary": snapshot.get("summary"),
            "confidence": snapshot.get("confidence"),
            "ambiguities": snapshot["ambiguities"],
            "labels": snapshot["labels"],
            "evidence": evidence,
            "retrieval_digest": snapshot["retrieval_digest"],
            "binding_fingerprint": snapshot["binding_fingerprint"],
            "approval": approval,
        }
        service_payload = {
            "retrieval": dict(retrieval),
            "labels": dict(snapshot),
            "approval": dict(approval),
        }
        if approval_error_code is not None:
            service_payload["approval_error_code"] = approval_error_code
        if approval["status"] == "awaiting_confirmation":
            approval_request = build_query_approval_request(snapshot, tool_call_id=tool_call_id)
            artifact["human_input"] = approval_request
            service_payload["approval_request"] = approval_request
        service_state = make_service_state(
            turn_id=turn_id,
            stage=("awaiting_confirmation" if approval["status"] == "awaiting_confirmation" else "cancelled" if approval["status"] == "cancelled" else "approved"),
            snapshot_id=snapshot["snapshot_id"],
            data_source_id=self._service_ability.data_source_id,
            payload=service_payload,
        )
        return artifact, service_state, approval["status"] == "awaiting_confirmation"

    @staticmethod
    def _emit_stream_event(payload: dict[str, Any]) -> None:
        """向 custom stream 发布用户侧标签事件。

        Args:
            payload: 当前完整标签快照。

        Return:
            None。
        """
        try:
            get_stream_writer()(
                {
                    "type": "data_query_labels",
                    "labels": payload,
                }
            )
        except Exception:
            logger.debug("查询标签 custom stream 输出失败。", exc_info=True)

    def _handle_query_labels(self, request: ToolCallRequest) -> ToolMessage | Command:
        """处理标签工具调用并返回状态更新。

        Args:
            request: 工具调用请求。

        Return:
            成功时返回不终止图执行的 Command，失败时返回错误 ToolMessage。
        """
        if self._service_ability is not None:
            try:
                payload, service_state, awaiting_confirmation = self._build_service_payload(request)
            except ValueError as exc:
                return self._error(request, str(exc))
            tool_call_id = str(request.tool_call.get("id") or "")
            message = ToolMessage(
                id=self._message_id(tool_call_id, payload),
                content=json.dumps({"ok": True, **payload}, ensure_ascii=False),
                tool_call_id=tool_call_id or "missing-tool-call-id",
                name=_PUBLISH_QUERY_LABELS_TOOL_NAME,
                status="success",
                artifact=payload,
            )
            self._emit_stream_event(payload)
            update: dict[str, Any] = {"messages": [message], "service_states": [service_state]}
            if awaiting_confirmation:
                # ADD: 复用 DeerFlow human-input v1，确认期间终止当前图运行。
                return Command(update=update, goto=END)
            return Command(update=update)

        try:
            payload = self._build_payload(request)
        except ValueError as exc:
            return self._error(request, str(exc))

        tool_call_id = str(request.tool_call.get("id") or "")
        message = ToolMessage(
            id=self._message_id(tool_call_id, payload),
            content=json.dumps({"ok": True, **payload}, ensure_ascii=False),
            tool_call_id=tool_call_id or "missing-tool-call-id",
            name=_PUBLISH_QUERY_LABELS_TOOL_NAME,
            status="success",
            artifact=payload,
        )
        self._emit_stream_event(payload)
        update: dict[str, Any] = {
            "messages": [message],
            "data_query_labels": payload,
        }
        if self._stage_name is not None:
            update["data_agent_stage"] = self._stage_name
        return Command(update=update)

    @override
    def after_model(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """同步模型返回后只保留一个标签发布调用。"""
        return keep_first_matching_tool_call(
            state,
            lambda tool_call: tool_call.get("name") == _PUBLISH_QUERY_LABELS_TOOL_NAME,
        )

    @override
    async def aafter_model(self, state: Mapping[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        """异步模型返回后复用标签发布串行化规则。"""
        return keep_first_matching_tool_call(
            state,
            lambda tool_call: tool_call.get("name") == _PUBLISH_QUERY_LABELS_TOOL_NAME,
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步拦截标签工具调用。

        Args:
            request: 工具调用请求。
            handler: 原工具执行器。

        Return:
            标签状态更新，或其他工具的原执行结果。
        """
        if request.tool_call.get("name") != _PUBLISH_QUERY_LABELS_TOOL_NAME:
            return handler(request)
        return self._handle_query_labels(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步拦截标签工具调用。

        Args:
            request: 工具调用请求。
            handler: 原异步工具执行器。

        Return:
            标签状态更新，或其他工具的原执行结果。
        """
        if request.tool_call.get("name") != _PUBLISH_QUERY_LABELS_TOOL_NAME:
            return await handler(request)
        return self._handle_query_labels(request)
