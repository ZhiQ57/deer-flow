"""DataAgent SQLRAG 单工具适配测试。"""

from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig
from deerflow.agents.service_agent.registry import DataAgentServiceAbility
from deerflow.agents.service_agent.sqlrag_contract import SQLRAG_OPERATIONS, is_sqlrag_retrieval_tool_name
from deerflow.tools.mcp_metadata import tag_mcp_tool


def _config() -> DataQueryServiceAbilityConfig:
    """构造测试用 DataAgent 能力配置。

    Returns:
        绑定 PostgreSQL 同源检索和执行目标的配置。
    """
    return DataQueryServiceAbilityConfig.model_validate(
        {
            "type": "data_query",
            "version": 1,
            "enable_sql_rag": True,
            "table_rag_config": "tablerag.yaml",
            "data_source_id": "sales-pg",
            "confirmation_mode": "auto",
            "sql_subagent_name": "sql-subagent",
            "sql_execution": {
                "enabled": True,
                "database_type": "postgresql",
                "dsn_env": "DATA_AGENT_SQL_DSN",
                "readonly": True,
                "allowed_schemas": ["public"],
            },
        }
    )


def test_sqlrag_tool_name_is_exact_and_operations_are_closed() -> None:
    """唯一工具名和六种 operation 必须保持封闭合同。"""
    assert is_sqlrag_retrieval_tool_name("sqlrag_retrieve") is True
    assert is_sqlrag_retrieval_tool_name("tablerag_sqlrag_retrieve") is False
    assert is_sqlrag_retrieval_tool_name("tablerag_retrieve") is False
    assert SQLRAG_OPERATIONS == {
        "hybrid-search",
        "search-evidences",
        "search-tables",
        "search-columns",
        "search-values",
        "expand-join-graph",
    }


def test_service_ability_removes_legacy_sqlrag_tools() -> None:
    """正式 DataAgent 工具面只保留唯一 SQLRAG 工具。"""

    def _tool(name: str) -> StructuredTool:
        """构造测试工具。

        Args:
            name: 工具名。

        Returns:
            可标记为 MCP 的结构化工具。
        """
        return StructuredTool.from_function(lambda: "ok", name=name, description=name)

    read_file = _tool("read_file")
    sqlrag = tag_mcp_tool(_tool("sqlrag_retrieve"))
    prefixed = tag_mcp_tool(_tool("tablerag_sqlrag_retrieve"))
    legacy = tag_mcp_tool(_tool("tablerag_search_tables"))
    other_mcp = tag_mcp_tool(_tool("postgres_query"))

    filtered = DataAgentServiceAbility(_config()).filter_tools([read_file, prefixed, legacy, sqlrag, other_mcp])

    assert [tool.name for tool in filtered] == ["read_file", "sqlrag_retrieve", "postgres_query"]


def test_service_ability_fails_closed_without_exact_sqlrag_tool() -> None:
    """只有旧工具或前缀工具时，正式 DataAgent 必须拒绝装配。"""
    prefixed = tag_mcp_tool(StructuredTool.from_function(lambda: "ok", name="tablerag_sqlrag_retrieve", description="legacy"))

    with pytest.raises(RuntimeError, match="sqlrag_retrieve"):
        DataAgentServiceAbility(_config()).filter_tools([prefixed])


def test_service_ability_fails_closed_with_duplicate_exact_sqlrag_tools() -> None:
    """多个无前缀同名工具会造成执行目标歧义，正式 DataAgent 必须拒绝装配。"""
    first = tag_mcp_tool(StructuredTool.from_function(lambda: "ok", name="sqlrag_retrieve", description="first"))
    second = tag_mcp_tool(StructuredTool.from_function(lambda: "ok", name="sqlrag_retrieve", description="second"))

    with pytest.raises(RuntimeError, match="exactly one"):
        DataAgentServiceAbility(_config()).filter_tools([first, second])
