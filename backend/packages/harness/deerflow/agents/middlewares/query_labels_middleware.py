"""用户意图标签的展示中间件.
暂时供 DeerFlow DataAgent 使用，后续可默认设置 Lead-Agent .


"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
import secrets
from typing import Any, Sequence, override

from deerflow.agents.human_input import read_human_input_response
from deerflow.agents.human_input import read_human_input_response
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.agents.service_agent.data_agent.service_config import DataAgentServiceAbilityConfig

logger = logging.getLogger(__name__)

_PUBLISH_QUERY_LABELS_TOOL_NAME = "publish_query_labels"  # 标签发布工具名称
_LABEL_SOURCES = frozenset(
    {
        "user",      # 用户直接意图标签
        "database",  # 数据库检索结果映射的标签
        "derived",   # 模型推理生成的标签
    }
)
_FLOW_ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

class QueryLabelsMiddleware(AgentMiddleware):
    """拦截标签声明工具并把结构化标签写入 runtime 状态。"""

    def __init__(
        self,
        *,
        service_ability: object | None = None,
    ) -> None:
        """初始化查询标签 middleware

        Args:
            service_ability: 当前 DataAgent service ability 配置，用于绑定数据源和合同版本

        Return:
            None
        """
        super().__init__()
        # ADD: 保存当前 service ability，后续标签快照必须绑定同一能力版本和数据源。
        self._service_ability = service_ability

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步拦截标签工具调用

        Args:
            request: 工具调用请求
            handler: 原工具执行器

        Return:
            标签状态更新，或其他工具的原执行结果
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
        """异步拦截标签工具调用

        Args:
            request: 工具调用请求
            handler: 原异步工具执行器

        Return:
            标签状态更新，或其他工具的原执行结果
        """
        if request.tool_call.get("name") != _PUBLISH_QUERY_LABELS_TOOL_NAME:
            return await handler(request)
        return self._handle_query_labels(request)


    # 主入口
    def _handle_query_labels(self, request: ToolCallRequest) -> ToolMessage | Command:
        """处理 `publish_query_labels` 标签工具调用结果, 结构化装配.
        注意: 模型使用`publish_query_labels`工具的参数包含了 意图标签|意图摘要 等.
        Args:
            request: `publish_query_labels`工具调用请求.

        Return:
            装配结构化标签的 ToolMessage
        """
        # 获取工具ID
        tool_call_id = str(request.tool_call.get("id") or "")

        try:
            # 整理为结构化字段
            payload = self._extract_query_labels(request)
        except ValueError as exc:
            return self._error(request, str(exc))

        # 获取审批决策
        approval = payload.get("approval") if isinstance(payload.get("approval"), Mapping) else {}

        # 构造 publish_query_labels 工具结果(ToolMessgae.content)
        content = {
            "ok": True,
            "flow_id": approval.get("flow_id", f"miss-flow-id-{self._build_flow_id()}"),      # TODO: 未来可绑定当前会话的 flow_id
            "approval_required": approval.get("required", False),
            "approval_reason": approval.get("reason", {}),
            "next_tool": approval.get("next_tool", None),
        }
        
        message = ToolMessage(
            id=self._message_id(tool_call_id, payload),
            tool_call_id=tool_call_id or "missing-tool-call-id",
            name=_PUBLISH_QUERY_LABELS_TOOL_NAME,
            status="success",
            content=json.dumps(content, ensure_ascii=False),
            artifact=payload,
        )
        
        self._emit_stream_event(payload)
        update: dict[str, Any] = {"messages": [message]}
        return Command(update=update)

    # ADD: 提取标签
    def _extract_query_labels(self, request: ToolCallRequest) -> dict[str, Any]:
        """提取工具请求的参数做结构化装配.

        Args:
            request: 当前 publish_query_labels 工具请求。

        Returns:
            artifact 和 service_states 快照。
        """
        # 获取 `publish_query_labels` 工具请求参数
        args = request.tool_call.get("args") or {}

        if not isinstance(args, Mapping):
            raise ValueError("标签工具参数非结构化")
        
        # 提取 labels 参数
        labels = args.get("labels")
        if isinstance(labels, str):
            try:
                labels = json.loads(labels)
            except json.JSONDecodeError as exc:
                raise ValueError("labels 必须是 JSON 数组") from exc
        if not isinstance(labels, list):
            raise ValueError("labels 必须是数组")

        # labels 归一化
        normalized_labels: list[Mapping[str, Any]] = []
        for index, item in enumerate(labels):
            if hasattr(item, "model_dump"):
                item = item.model_dump(exclude_none=True)
            if not isinstance(item, Mapping):
                raise ValueError(f"labels[{index}] 必须是对象。")
            normalized_labels.append(dict(item))
        
        # ADD: 计算本次标签发布后的审批决策。
        approval = self._decide_query_approval(self._service_ability, args)

        artifact: dict[str, Any] = {
            "intent": args.get("intent") or "",
            "summary": args.get("summary"),
            "labels": normalized_labels,
            "evidence": [],
            "approval": approval
        }

        # service_payload = {
        #     "labels": normalized_labels,
        #     "review_items": build_query_review_items(args),
        #     "approval": approval
        # }

        # # ADD: 创建活动状态
        # service_state = make_service_state(
        #     stage="labels_published",
        #     data_source_id=self._service_ability.data_source_id,
        #     payload=service_payload,
        # )
        return artifact



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
            normalized_labels.append(record)
        return normalized_labels


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


    # ADD: 判断意图是否需要人工审批
    @staticmethod
    def _decide_query_approval(
        self,
        config: DataAgentServiceAbilityConfig | None,
        args: Any,
    ) -> dict[str, Any]:
        """根据审批配置和模型判断，生成本轮标签发布后的审批决策。"""
        model_requested_approval = bool(args.get("approval_required"))

        flow_id = self._build_flow_id()  # 生成新的 flow_id

        if config is not None and config.confirmation_mode == "always":
            return {
                "flow_id": flow_id,
                "required": True,
                "reason": "当前审批配置要求必须进行人工审批,调用 ask_intent_approval 工具",
                "next_tool": "ask_intent_approval",
            }

        if (
            (config is None or config.confirmation_mode == "on_ambiguity")
            and model_requested_approval
        ):
            return {
                "flow_id": flow_id,
                "required": True,
                "reason": "存在未消解歧义，需要人类审批,请调用 ask_intent_approval 工具",
                "next_tool": "ask_intent_approval",
            }

        return {
            "flow_id": flow_id,
            "required": False,
            "reason": "当前意图和标签无需人工审批，可以继续下一步。",
            "next_tool": None,
        }

    def _build_flow_id(self) -> str:
        """生成一个新的 flow_id，用于标识当前意图标签发布的审批流程。
        Return:
            新的 flow_id 字符串。
        """

        value = int.from_bytes(secrets.token_bytes(5), "big")
        chars = []
        for _ in range(8):
            chars.append(_FLOW_ID_ALPHABET[value & 31])
            value >>= 5
        return "".join(reversed(chars))