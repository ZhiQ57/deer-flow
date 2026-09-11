"""DataAgent能力实现类"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class DataAgentServiceAbility:
    """DataAgent 查询闭环的正式 service ability 实现。"""

    name = "data_query"

    def __init__(self, config: dict) -> None:
        """初始化 DataAgent 能力适配器。

        Args:
            config: 已经通过 Pydantic 合同校验的能力配置。
        """
        self.config = config

    def build_tools(self) -> list[BaseTool]:
        """返回 DataAgent 当前阶段允许的业务工具。"""
        from deerflow.tools.builtins.ask_intent_approval_tool import ask_intent_approval_tool
        from deerflow.tools.builtins.query_labels_tool import publish_query_labels_tool

        return [publish_query_labels_tool, ask_intent_approval_tool]

    def filter_tools(self, tools: list[BaseTool]) -> list[BaseTool]:
        """收敛 DataAgent 的 SQLRAG MCP 工具面。

        Args:
            tools: DeerFlow 已加载的基础、MCP 和社区工具。

        Returns:
            移除旧 TableRAG 多工具合同后的工具列表。

        Raises:
            RuntimeError: 名称严格等于 ``sqlrag_retrieve`` 的 MCP 工具不是唯一一个。
        """
        from deerflow.tools.mcp_metadata import is_mcp_tool

        # TODO: 删除
        SQLRAG_RETRIEVE_TOOL_NAME = "sqlrag_retrieve"
        # TODO: 删除
        _LEGACY_SQLRAG_TOOL_NAMES = (
            "tablerag_retrieve",
            "tablerag_raw_retrieve",
            "tablerag_search_evidences",
            "tablerag_search_tables",
            "tablerag_search_columns",
            "tablerag_search_values",
            "tablerag_expand_join_graph",
            "tablerag_validate_index",
            "tablerag_initialize_indexes",
            "tablerag_sync_field_values",
        )

        filtered: list[BaseTool] = []
        sqlrag_count = 0
        for tool in tools:
            if not is_mcp_tool(tool):
                filtered.append(tool)
                continue
            if isinstance(tool.name, str) and tool.name == SQLRAG_RETRIEVE_TOOL_NAME:
                sqlrag_count += 1
                filtered.append(tool)
                continue
            if tool.name.endswith(f"_{SQLRAG_RETRIEVE_TOOL_NAME}") or any(tool.name == legacy or tool.name.endswith(f"_{legacy}") for legacy in _LEGACY_SQLRAG_TOOL_NAMES):
                continue
            filtered.append(tool)
        if sqlrag_count == 0:
            raise RuntimeError("DataAgent requires the exact MCP tool name 'sqlrag_retrieve'. Set mcpServers.tablerag.tool_name_prefix=false and refresh the MCP tool cache.")
        if sqlrag_count > 1:
            raise RuntimeError("DataAgent requires exactly one MCP tool named 'sqlrag_retrieve'. Disable duplicate unprefixed MCP servers before building the agent.")
        return filtered

    def build_middlewares(self) -> list[AgentMiddleware]:
        """返回 DataAgent 当前阶段的业务 middleware。"""
        from deerflow.agents.middlewares.query_labels_middleware import QueryLabelsMiddleware

        from .query_intent_approval_middleware import QueryIntentApprovalMiddleware

        # ADD: 业务 middleware 只挂在 DataAgent 适配器，默认 lead-agent 不受影响。
        return [
            QueryLabelsMiddleware(service_ability=self.config),
            QueryIntentApprovalMiddleware(self.config),
        ]

    def public_metadata(self) -> dict[str, Any]:
        """返回能力的脱敏前端/运行 metadata。"""
        return self.config.public_metadata()
