"""DataAgent service ability 注册表。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

from config import DataQueryServiceAbilityConfig, parse_service_ability

logger = logging.getLogger(__name__)


class ServiceAbilityAdapter(Protocol):
    """业务能力适配器协议。"""

    name: str

    def filter_tools(self, tools: list[BaseTool]) -> list[BaseTool]: ...

    def build_tools(self) -> list[BaseTool]: ...

    def build_middlewares(self) -> list[AgentMiddleware]: ...

    def public_metadata(self) -> dict[str, Any]: ...


class DataAgentServiceAbility:
    """DataAgent 查询闭环的正式 service ability 实现。"""

    # ADD: 提供 DataAgent 专属工具和 middleware，避免污染默认 lead-agent。
    name = "data_query"

    def __init__(self, config: DataQueryServiceAbilityConfig) -> None:
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
        from deerflow.agents.service_agent.sqlrag_contract import is_legacy_sqlrag_tool_name, is_sqlrag_retrieval_tool_name
        from deerflow.tools.mcp_metadata import is_mcp_tool

        filtered: list[BaseTool] = []
        sqlrag_count = 0
        for tool in tools:
            if not is_mcp_tool(tool):
                filtered.append(tool)
                continue
            if is_sqlrag_retrieval_tool_name(tool.name):
                sqlrag_count += 1
                filtered.append(tool)
                continue
            if is_legacy_sqlrag_tool_name(tool.name):
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
        from .sql_stage_middleware import SqlStageMiddleware

        # ADD: 业务 middleware 只挂在 DataAgent 适配器，默认 lead-agent 不受影响。
        return [
            QueryLabelsMiddleware(service_ability=self.config),
            QueryIntentApprovalMiddleware(self.config),
            SqlStageMiddleware(self.config),
        ]

    def public_metadata(self) -> dict[str, Any]:
        """返回能力的脱敏前端/运行 metadata。"""
        return self.config.public_metadata()

def resolve_service_ability_safely(raw: Mapping[str, Any] | None) -> ServiceAbilityAdapter | None:
    """安全解析 custom-agent 的 service ability。

    Args:
        raw: ``AgentConfig.service_ability`` 原始配置。

    Returns:
        已解析的能力适配器；配置错误时记录脱敏信息并返回 None。
    """
    try:
        config = parse_service_ability(raw)

        if config is None:
            return None
        
        if config.type == "data_query" and config.version == 1:
            return DataAgentServiceAbility(config)
        
        raise ValueError(f"不支持的 service_ability 合同：{config.type}/v{config.version}")
    
    except (TypeError, ValueError) as exc:
        issues: list[str] = []
        errors = getattr(exc, "errors", None)

        if callable(errors):
            for error in errors(include_url=False, include_input=False):
                location = ".".join(str(part) for part in error.get("loc", ())) or "service_ability"
                issues.append(f"{location}: 值不符合 {error.get('type', '配置')} 约束")

        if not issues:
            issues.append("service_ability: 配置类型或版本不受支持")
        logger.error("DataAgent service_ability 配置无效（%s）".join(issues))
        return None
