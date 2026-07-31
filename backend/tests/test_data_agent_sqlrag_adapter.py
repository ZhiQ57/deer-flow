"""DataAgent SQLRAG 单工具适配测试。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest

from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig
from deerflow.agents.service_agent.registry import DataAgentServiceAbility
from deerflow.agents.service_agent.sqlrag_contract import SQLRAG_OPERATIONS, is_sqlrag_retrieval_tool_name
from deerflow.agents.service_agent.state import build_retrieval_context
from deerflow.agents.service_agent.table_rag_middleware import TableRagStageMiddleware
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


def _binding() -> dict[str, Any]:
    """构造不含 Secret 的测试绑定。

    Returns:
        DataAgent 检索上下文接受的绑定对象。
    """
    return {
        "version": 1,
        "data_source_id": "sales-pg",
        "database_type": "postgresql",
        "source_binding_mode": "same_physical_target",
        "table_rag_config_ref": "tablerag.yaml",
        "retrieval_target_fingerprint": "sha256:target",
        "execution_target_fingerprint": "sha256:target",
        "binding_fingerprint": "sha256:binding",
        "allowed_schemas": ["public"],
    }


def _request(name: str = "sqlrag_retrieve") -> ToolCallRequest:
    """构造 SQLRAG middleware 工具请求。

    Args:
        name: 工具名。

    Returns:
        带完整混合检索参数的工具请求。
    """
    runtime = MagicMock()
    runtime.context = {
        "thread_id": "thread-1",
        "data_query_binding": _binding(),
    }
    return ToolCallRequest(
        tool_call={
            "name": name,
            "args": {
                "operation": "hybrid-search",
                "query": "查询销售额",
            },
            "id": "sqlrag-call-1",
        },
        tool=None,
        state={"messages": [HumanMessage(id="turn-1", content="查询销售额")]},
        runtime=runtime,
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


@pytest.mark.parametrize(
    ("operation", "result", "request_args", "collection"),
    [
        (
            "hybrid-search",
            {"query": "查询销售额", "tables": [{"table_name": "orders"}]},
            {"operation": "hybrid-search", "query": "查询销售额"},
            "tables",
        ),
        (
            "search-evidences",
            [{"evidence_content": "销售额为 SUM(amount)"}],
            {"operation": "search-evidences", "queries": ["销售额"]},
            "evidences",
        ),
        (
            "search-tables",
            [{"table_name": "orders"}],
            {"operation": "search-tables", "queries": ["订单"]},
            "tables",
        ),
        (
            "search-columns",
            [{"table_name": "orders", "column_name": "amount"}],
            {"operation": "search-columns", "queries": ["销售金额"]},
            "columns",
        ),
        (
            "search-values",
            [{"table_name": "orders", "column_name": "region", "value": "华东"}],
            {"operation": "search-values", "queries": ["华东"]},
            "values",
        ),
        (
            "expand-join-graph",
            [{"left_table": "orders", "right_table": "customers"}],
            {"operation": "expand-join-graph", "table_names": ["orders", "customers"]},
            "join_graphs",
        ),
    ],
)
def test_build_retrieval_context_supports_all_sqlrag_operations(
    operation: str,
    result: object,
    request_args: dict[str, Any],
    collection: str,
) -> None:
    """六种 operation 都应登记到统一 Evidence registry。"""
    retrieval = build_retrieval_context(
        {
            "ok": True,
            "operation": operation,
            "result": result,
        },
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
        request_args=request_args,
    )

    assert retrieval["operation"] == operation
    assert retrieval["tool_name"] == "sqlrag_retrieve"
    assert retrieval[collection]
    assert retrieval["registry"]
    if operation.startswith("search-"):
        assert retrieval["keyword_queries"] == request_args["queries"]


def test_build_retrieval_context_rejects_prefixed_tool_name() -> None:
    """正式状态投影不能绕过 middleware 登记带 Server 前缀的工具结果。"""
    with pytest.raises(ValueError, match="sqlrag_retrieve"):
        build_retrieval_context(
            {
                "ok": True,
                "operation": "search-tables",
                "result": [{"table_name": "orders"}],
            },
            tool_name="tablerag_sqlrag_retrieve",
            turn_id="turn-1",
            data_source_id="sales-pg",
            binding=_binding(),
            request_args={"operation": "search-tables", "queries": ["订单"]},
        )


def test_table_rag_middleware_serializes_exact_sqlrag_calls() -> None:
    """同一模型响应中的 SQLRAG 调用必须只保留第一个。"""
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sqlrag_retrieve",
                        "id": "sqlrag-1",
                        "args": {"operation": "hybrid-search", "query": "销售额"},
                    },
                    {
                        "name": "sqlrag_retrieve",
                        "id": "sqlrag-2",
                        "args": {"operation": "search-values", "queries": ["华东"]},
                    },
                    {
                        "name": "tablerag_sqlrag_retrieve",
                        "id": "prefixed-legacy",
                        "args": {"operation": "hybrid-search", "query": "销售额"},
                    },
                ],
            )
        ]
    }

    update = TableRagStageMiddleware(_config()).after_model(state, MagicMock())

    assert [item["id"] for item in update["messages"][0].tool_calls] == ["sqlrag-1", "prefixed-legacy"]


def test_table_rag_middleware_projects_sqlrag_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """SQLRAG 成功结果必须写入正式 service_states 检索快照。"""
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", "postgresql://readonly:secret@db.local:5432/sales")
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@db.local:5432/sales")
    payload = {
        "ok": True,
        "operation": "hybrid-search",
        "result": {
            "query": "查询销售额",
            "tables": [{"table_name": "orders"}],
            "columns": [{"table_name": "orders", "column_name": "amount"}],
        },
    }
    message = ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id="sqlrag-call-1",
        name="sqlrag_retrieve",
    )

    result = TableRagStageMiddleware(_config()).wrap_tool_call(
        _request(),
        lambda _request: message,
    )

    service_state = result.update["service_states"][0]
    retrieval = service_state["payload"]["retrieval"]
    assert service_state["stage"] == "retrieving"
    assert retrieval["tool_name"] == "sqlrag_retrieve"
    assert retrieval["operation"] == "hybrid-search"
    assert retrieval["query"] == "查询销售额"


@pytest.mark.parametrize("as_text_blocks", [False, True])
def test_table_rag_middleware_projects_sqlrag_result_from_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
    as_text_blocks: bool,
) -> None:
    """SQLRAG 成功结果无论是字符串还是 text-block 列表都必须写入正式 service_states 快照。"""
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", "postgresql://readonly:secret@db.local:5432/sales")
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@db.local:5432/sales")
    payload = {
        "ok": True,
        "operation": "hybrid-search",
        "result": {
            "query": "查询销售额",
            "tables": [{"table_name": "orders"}],
            "columns": [{"table_name": "orders", "column_name": "amount"}],
        },
    }
    content: object = json.dumps(payload, ensure_ascii=False)
    if as_text_blocks:
        content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    message = ToolMessage(
        content=content,
        tool_call_id="sqlrag-call-1",
        name="sqlrag_retrieve",
    )

    result = TableRagStageMiddleware(_config()).wrap_tool_call(
        _request(),
        lambda _request: message,
    )

    service_state = result.update["service_states"][0]
    retrieval = service_state["payload"]["retrieval"]
    assert service_state["stage"] == "retrieving"
    assert retrieval["tool_name"] == "sqlrag_retrieve"
    assert retrieval["operation"] == "hybrid-search"
    assert retrieval["query"] == "查询销售额"
