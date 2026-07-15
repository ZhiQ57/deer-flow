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
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

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

    @staticmethod
    def _build_payload(request: ToolCallRequest) -> dict[str, Any]:
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

        labels = QueryLabelsMiddleware._normalize_labels(args.get("labels"))
        if any(item["source"] == "database" for item in labels) and not QueryLabelsMiddleware._has_retrieval_evidence(request):
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

    @classmethod
    def _handle_query_labels(cls, request: ToolCallRequest) -> ToolMessage | Command:
        """处理标签工具调用并返回状态更新。

        Args:
            request: 工具调用请求。

        Return:
            成功时返回不终止图执行的 Command，失败时返回错误 ToolMessage。
        """
        try:
            payload = cls._build_payload(request)
        except ValueError as exc:
            return cls._error(request, str(exc))

        tool_call_id = str(request.tool_call.get("id") or "")
        message = ToolMessage(
            id=cls._message_id(tool_call_id, payload),
            content=json.dumps({"ok": True, **payload}, ensure_ascii=False),
            tool_call_id=tool_call_id or "missing-tool-call-id",
            name=_PUBLISH_QUERY_LABELS_TOOL_NAME,
            status="success",
            artifact=payload,
        )
        cls._emit_stream_event(payload)
        return Command(
            update={
                "messages": [message],
                "data_query_labels": payload,
            }
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
