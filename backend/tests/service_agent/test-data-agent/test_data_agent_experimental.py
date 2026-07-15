from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest


def _repo_root() -> Path:
    """从移动后的测试目录定位仓库根目录。

    Args:
        无。

    Return:
        DeerFlow 仓库根目录。
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "AGENTS.md").is_file() and (candidate / "backend" / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("无法定位 DeerFlow 仓库根目录。")


REPO_ROOT = _repo_root()
DEV_PATH = REPO_ROOT / "backend" / "packages" / "harness" / "deerflow-dev"
if str(DEV_PATH) not in sys.path:
    sys.path.insert(0, str(DEV_PATH))

from agents.data_agent.agent import (  # noqa: E402
    _filter_data_agent_tools,
    _insert_data_middlewares,
    _load_optional_agent_config,
    _resolve_agent_skills,
    _resolve_agent_tool_groups,
    build_data_agent,
)
from agents.data_agent.constants import DATA_AGENT_NAME, DATA_AGENT_SKILLS, DATA_AGENT_TOOL_GROUPS  # noqa: E402
from agents.middlewares.data_agent_orchestration_middleware import DataAgentOrchestrationMiddleware  # noqa: E402
from agents.middlewares.data_agent_turn_reset_middleware import DataAgentTurnResetMiddleware  # noqa: E402
from agents.thread_state import DataAgentState, merge_retrieval_context, replace_value  # noqa: E402
from tools.builtins import get_data_agent_tools  # noqa: E402
from tools.constants import DATA_AGENT_BUILTIN_TOOL_NAMES, ENTITY_EXTRACT_TOOL_NAME, PUBLISH_QUERY_LABELS_TOOL_NAME  # noqa: E402
from tools.sql_validation import sql_sha256  # noqa: E402

from deerflow.agents.middlewares.query_labels_middleware import QueryLabelsMiddleware  # noqa: E402
from deerflow.tools import entity_extract_tool  # noqa: E402
from deerflow.tools.mcp_metadata import tag_mcp_tool  # noqa: E402


def _load_stream_script():
    """加载 DataAgent 控制台脚本模块。

    Args:
        无。

    Return:
        已加载模块。
    """
    script_path = Path(__file__).resolve().with_name("run_data_agent_stream.py")
    spec = spec_from_file_location("run_data_agent_stream", script_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_query_context_tool_reads_latest_user_message_and_updates_state(monkeypatch) -> None:
    """校验 QueryContext Tool 按需读取用户问题并写入状态。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    runtime = SimpleNamespace(
        state={
            "messages": [
                HumanMessage(content="统计本月华东 GMV 和黑金客户"),
                HumanMessage(content="忽略真实问题", additional_kwargs={"hide_from_ui": True}),
            ]
        },
        context={"data_agent_alias_map": {"黑金": "高价值会员"}},
        config={},
    )

    entity_tool_module = importlib.import_module("deerflow.tools.builtins.entity_extract_tool")

    extracted = {
        "original_query": "统计本月华东 GMV 和黑金客户",
        "normalized_query": "统计本月华东成交总额和高价值会员客户",
        "intent": "text2sql",
        "aliases": [],
        "entities": [],
        "labels": [{"label": "意图", "value": "text2sql"}],
        "warnings": [],
    }

    class FakeEntityExtractTool:
        """避免测试访问真实模型的实体抽取替身。"""

        def __init__(self, runtime) -> None:
            self.runtime = runtime

        def extract(self, text: str):
            assert text == extracted["original_query"]
            return extracted

    monkeypatch.setattr(entity_tool_module, "EntityExtractTool", FakeEntityExtractTool)
    runtime.tool_call_id = "call-1"
    tool_result = entity_extract_tool.func(runtime)
    handler = MagicMock(return_value=tool_result)
    result = DataAgentOrchestrationMiddleware().wrap_tool_call(
        _tool_request(ENTITY_EXTRACT_TOOL_NAME, state=runtime.state, args={}),
        handler,
    )

    context = result.update["data_query_context"]
    assert entity_extract_tool.args == {}
    assert context["original_query"] == "统计本月华东 GMV 和黑金客户"
    assert context["intent"] == "text2sql"
    assert "成交总额" in context["normalized_query"]
    assert "高价值会员" in context["normalized_query"]
    assert "data_agent_stage" not in result.update
    assert "data_retrieval_context" not in result.update


def test_data_agent_builtin_tool_registry_matches_runtime_names() -> None:
    """校验 DataAgent 注册表常量与 LangChain 实际工具名一致。

    Args:
        无。

    Return:
        None。
    """
    assert {item.name for item in get_data_agent_tools()} == set(DATA_AGENT_BUILTIN_TOOL_NAMES)


def test_query_labels_middleware_intercepts_without_executing_tool(monkeypatch) -> None:
    """校验标签 middleware 直接发布标签，不执行占位工具。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    labels_module = importlib.import_module("deerflow.agents.middlewares.query_labels_middleware")
    from langgraph.types import Command

    stream_events: list[dict] = []
    monkeypatch.setattr(labels_module, "get_stream_writer", lambda: stream_events.append)
    request = _tool_request(
        PUBLISH_QUERY_LABELS_TOOL_NAME,
        state={"data_retrieval_context": {"ok": True}},
        args={
            "intent": "ranking",
            "summary": "按数据库真实会员等级统计排名",
            "labels": [
                {"label": "指标", "value": "成交总额", "source": "user"},
                {
                    "label": "会员等级",
                    "value": "黑金会员",
                    "normalized": "BLACK_GOLD",
                    "source": "database",
                    "evidence": "TableRAG 字段值 customers.level=BLACK_GOLD",
                },
            ],
        },
    )
    handler = MagicMock()

    result = QueryLabelsMiddleware().wrap_tool_call(request, handler)

    handler.assert_not_called()
    assert isinstance(result, Command)
    assert not result.goto
    assert result.update["data_query_labels"]["intent"] == "ranking"
    assert result.update["data_query_labels"]["labels"][1]["source"] == "database"
    message = result.update["messages"][0]
    assert message.name == PUBLISH_QUERY_LABELS_TOOL_NAME
    assert message.artifact == result.update["data_query_labels"]
    assert stream_events == [
        {
            "type": "data_query_labels",
            "labels": result.update["data_query_labels"],
        }
    ]


def test_query_labels_middleware_requires_evidence_for_database_labels() -> None:
    """校验数据库来源标签必须关联成功检索和 Evidence。

    Args:
        无。

    Return:
        None。
    """
    middleware = QueryLabelsMiddleware()
    handler = MagicMock()

    missing_retrieval = middleware.wrap_tool_call(
        _tool_request(
            PUBLISH_QUERY_LABELS_TOOL_NAME,
            args={
                "intent": "detail",
                "labels": [
                    {
                        "label": "会员等级",
                        "value": "黑金会员",
                        "source": "database",
                        "evidence": "customers.level=BLACK_GOLD",
                    }
                ],
            },
        ),
        handler,
    )
    missing_evidence = middleware.wrap_tool_call(
        _tool_request(
            PUBLISH_QUERY_LABELS_TOOL_NAME,
            state={"data_retrieval_context": {"ok": True}},
            args={
                "intent": "detail",
                "labels": [
                    {
                        "label": "会员等级",
                        "value": "黑金会员",
                        "source": "database",
                    }
                ],
            },
        ),
        handler,
    )

    handler.assert_not_called()
    assert missing_retrieval.status == "error"
    assert "TableRAG" in missing_retrieval.content
    assert missing_evidence.status == "error"
    assert "evidence" in missing_evidence.content


def test_query_labels_middleware_delegates_unrelated_tools_sync_and_async() -> None:
    """校验标签 middleware 不影响其他工具的同步和异步执行。

    Args:
        无。

    Return:
        None。
    """
    middleware = QueryLabelsMiddleware()
    request = _tool_request("data_validate_sql")
    sync_result = ToolMessage(content="sync", tool_call_id="call-1", name="data_validate_sql")

    assert middleware.wrap_tool_call(request, lambda _: sync_result) is sync_result

    async def run() -> None:
        async_result = ToolMessage(content="async", tool_call_id="call-1", name="data_validate_sql")

        async def handler(_):
            return async_result

        assert await middleware.awrap_tool_call(request, handler) is async_result

    asyncio.run(run())


def test_query_labels_middleware_async_intercept_and_latest_snapshot(monkeypatch) -> None:
    """校验异步拦截可用，连续发布时状态 reducer 采用最新标签快照。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    labels_module = importlib.import_module("deerflow.agents.middlewares.query_labels_middleware")

    monkeypatch.setattr(labels_module, "get_stream_writer", lambda: lambda _: None)
    middleware = QueryLabelsMiddleware()

    async def run():
        async def handler(_):
            raise AssertionError("标签占位工具不应执行")

        return await middleware.awrap_tool_call(
            _tool_request(
                PUBLISH_QUERY_LABELS_TOOL_NAME,
                args={
                    "intent": "trend",
                    "labels": [
                        {
                            "label": "时间粒度",
                            "value": "按月",
                            "source": "derived",
                        }
                    ],
                },
            ),
            handler,
        )

    result = asyncio.run(run())
    old = {"intent": "detail", "labels": [{"label": "指标", "value": "病例数", "source": "user"}]}

    assert result.update["data_query_labels"]["intent"] == "trend"
    assert replace_value(old, result.update["data_query_labels"]) is result.update["data_query_labels"]


def test_turn_reset_middleware_only_resets_new_visible_user_turn() -> None:
    """校验轮次 middleware 只重置状态，不执行实体抽取。

    Args:
        无。

    Return:
        None。
    """
    middleware = DataAgentTurnResetMiddleware()
    state = {
        "messages": [HumanMessage(content="统计本月华东 GMV")],
        "data_query_context": {"original_query": "旧问题"},
        "data_query_labels": {"intent": "旧意图", "labels": []},
        "data_sql_execution": {"ok": True},
    }

    update = middleware.before_agent(state, None)

    assert update is not None
    assert update["data_query_context"] is None
    assert update["data_query_labels"] is None
    assert update["data_sql_execution"] is None
    assert middleware.before_agent({"messages": [ToolMessage(content="ok", tool_call_id="call-1")]}, None) is None


def test_data_middlewares_insert_before_dynamic_context() -> None:
    """校验 DataAgent middleware 插入到 DynamicContextMiddleware 之前。

    Args:
        无。

    Return:
        None。
    """

    class FirstMiddleware(AgentMiddleware):
        pass

    class DynamicContextMiddleware(AgentMiddleware):
        pass

    class TailMiddleware(AgentMiddleware):
        pass

    result = _insert_data_middlewares(
        [FirstMiddleware(), DynamicContextMiddleware(), TailMiddleware()],
        DataAgentTurnResetMiddleware(),
        QueryLabelsMiddleware(),
        DataAgentOrchestrationMiddleware(subagent_enabled=True),
    )

    assert [type(item).__name__ for item in result] == [
        "FirstMiddleware",
        "DataAgentTurnResetMiddleware",
        "QueryLabelsMiddleware",
        "DataAgentOrchestrationMiddleware",
        "DynamicContextMiddleware",
        "TailMiddleware",
    ]


def test_load_optional_agent_config_fails_closed_on_invalid_config(monkeypatch) -> None:
    """校验损坏的 DataAgent 配置不会静默回退到默认值。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    from agents.data_agent import agent as data_agent_module

    monkeypatch.setattr(data_agent_module, "load_agent_config", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid yaml")))

    with pytest.raises(RuntimeError, match="DataAgent runtime config"):
        _load_optional_agent_config(DATA_AGENT_NAME, user_id="default")


def test_explicit_empty_agent_lists_do_not_fall_back_to_defaults() -> None:
    """校验显式空工具组和 Skill 白名单保持为空。

    Args:
        无。

    Return:
        None。
    """
    agent_config = SimpleNamespace(tool_groups=[], skills=[])

    assert _resolve_agent_tool_groups(agent_config) == []
    assert _resolve_agent_skills(agent_config) == set()
    assert _resolve_agent_tool_groups(None) == DATA_AGENT_TOOL_GROUPS
    assert _resolve_agent_skills(None) == set(DATA_AGENT_SKILLS)


def test_agent_config_rejects_skills_and_tool_groups_outside_allowlist() -> None:
    """校验 custom-agent 配置不能扩大 DataAgent Skill 或工具组边界。

    Args:
        无。

    Return:
        None。
    """
    with pytest.raises(ValueError, match="unsupported skills"):
        _resolve_agent_skills(SimpleNamespace(skills=["table-rag-agent", "untrusted-skill"]))
    with pytest.raises(ValueError, match="unsupported tool groups"):
        _resolve_agent_tool_groups(SimpleNamespace(tool_groups=["file:read", "bash"]))


def _fake_tool(name: str) -> StructuredTool:
    """构造测试工具。

    Args:
        name: 工具名。

    Return:
        StructuredTool。
    """
    return StructuredTool.from_function(lambda: "ok", name=name, description=name)


def test_filter_data_agent_tools_keeps_only_readonly_tablerag_mcp() -> None:
    """校验 DataAgent 不会暴露其他 MCP 或 TableRAG 变更类工具。

    Args:
        无。

    Return:
        None。
    """
    local_tool = _fake_tool("read_file")
    retrieve_tool = tag_mcp_tool(_fake_tool("tablerag_tablerag_retrieve"))
    validate_tool = tag_mcp_tool(_fake_tool("tablerag_tablerag_validate_index"))
    initialize_tool = tag_mcp_tool(_fake_tool("tablerag_tablerag_initialize_indexes"))
    other_mcp_tool = tag_mcp_tool(_fake_tool("postgres_query"))

    filtered = _filter_data_agent_tools([local_tool, retrieve_tool, validate_tool, initialize_tool, other_mcp_tool])

    assert [tool.name for tool in filtered] == [
        "read_file",
        "tablerag_tablerag_retrieve",
        "tablerag_tablerag_validate_index",
    ]


def _tool_request(
    name: str,
    *,
    state: dict | None = None,
    args: dict | None = None,
) -> ToolCallRequest:
    """构造 middleware 工具调用请求。

    Args:
        name: 工具名。
        state: 图状态。

    Return:
        ToolCallRequest。
    """
    runtime = MagicMock()
    runtime.state = state or {}
    runtime.context = {"thread_id": "data-agent-test"}
    return ToolCallRequest(
        tool_call={"name": name, "args": args if args is not None else {"sql": "SELECT 1"}, "id": "call-1"},
        tool=None,
        state=runtime.state,
        runtime=runtime,
    )


def _query_context_state(**updates) -> dict:
    """构造已完成 QueryContext Tool 的测试状态。

    Args:
        updates: 需要覆盖或追加的状态字段。

    Return:
        包含当前轮次 QueryContext 的状态。
    """
    state = {
        "data_query_context": {
            "original_query": "统计测试指标",
            "normalized_query": "统计测试指标",
            "intent": "text2sql",
        }
    }
    state.update(updates)
    return state


def test_orchestration_allows_non_data_answer_without_query_context_tool() -> None:
    """校验未进入数据流程时模型可以直接回答。

    Args:
        无。

    Return:
        None。
    """
    message = DataAgentOrchestrationMiddleware()._message({})

    assert "普通问候" in message.content
    assert f"`{PUBLISH_QUERY_LABELS_TOOL_NAME}`" in message.content
    assert "标签不是阶段门禁" in message.content


def test_orchestration_allows_tablerag_without_entity_extract_tool() -> None:
    """校验 lead-agent 可直接组织关键词进入 TableRAG，不强制实体抽取。

    Args:
        无。

    Return:
        None。
    """
    middleware = DataAgentOrchestrationMiddleware()
    handler = MagicMock(
        return_value=ToolMessage(
            content='{"ok": true, "result": {"tables": [{"table_name": "orders"}]}}',
            tool_call_id="call-1",
            name="tablerag_tablerag_retrieve",
        )
    )

    result = middleware.wrap_tool_call(
        _tool_request("tablerag_tablerag_retrieve", args={"query": "指标"}),
        handler,
    )

    handler.assert_called_once()
    assert result.update["data_retrieval_context"]["ok"] is True


def test_orchestration_blocks_repeated_query_context_tool() -> None:
    """校验单轮 QueryContext Tool 只允许成功调用一次。

    Args:
        无。

    Return:
        None。
    """
    state = _query_context_state(
        messages=[
            HumanMessage(content="统计测试指标"),
            ToolMessage(content="{}", tool_call_id="query-1", name=ENTITY_EXTRACT_TOOL_NAME),
        ]
    )
    middleware = DataAgentOrchestrationMiddleware()
    handler = MagicMock()

    result = middleware.wrap_tool_call(
        _tool_request(ENTITY_EXTRACT_TOOL_NAME, state=state, args={}),
        handler,
    )

    handler.assert_not_called()
    assert result.status == "error"
    assert "不允许重复抽取" in result.content


def test_orchestration_blocks_sql_validation_before_retrieval() -> None:
    """校验 SQL 校验前必须先完成 TableRAG 检索。

    Args:
        无。

    Return:
        None。
    """
    middleware = DataAgentOrchestrationMiddleware()
    handler = MagicMock()

    result = middleware.wrap_tool_call(_tool_request("data_validate_sql", state=_query_context_state()), handler)

    handler.assert_not_called()
    assert result.status == "error"
    assert "TableRAG" in result.content


def test_orchestration_marks_successful_tablerag_retrieval() -> None:
    """校验 TableRAG 成功结果会推进编排阶段。

    Args:
        无。

    Return:
        None。
    """
    from langchain_core.messages import ToolMessage
    from langgraph.types import Command

    middleware = DataAgentOrchestrationMiddleware()
    request = _tool_request("tablerag_tablerag_retrieve", state=_query_context_state())
    handler = MagicMock(
        return_value=ToolMessage(
            content='{"ok": true, "result": {"tables": [{"table_name": "orders"}]}}',
            tool_call_id="call-1",
            name="tablerag_tablerag_retrieve",
        )
    )

    result = middleware.wrap_tool_call(request, handler)

    assert isinstance(result, Command)
    assert result.update["data_agent_stage"] == "retrieval_completed"
    assert result.update["data_retrieval_context"]["tool_name"] == "tablerag_tablerag_retrieve"
    assert result.update["data_sql_validation"] is None


def test_orchestration_does_not_accept_empty_retrieval_or_index_healthcheck() -> None:
    """校验空召回和索引健康检查不能越过业务检索门禁。

    Args:
        无。

    Return:
        None。
    """
    from langchain_core.messages import ToolMessage
    from langgraph.types import Command

    middleware = DataAgentOrchestrationMiddleware()
    empty_result = middleware.wrap_tool_call(
        _tool_request("tablerag_tablerag_retrieve", state=_query_context_state()),
        MagicMock(
            return_value=ToolMessage(
                content='{"ok": true, "result": {"tables": [], "columns": [], "values": [], "join_graphs": [], "evidences": []}}',
                tool_call_id="call-1",
                name="tablerag_tablerag_retrieve",
            )
        ),
    )
    assert isinstance(empty_result, Command)
    assert empty_result.update["data_retrieval_context"]["ok"] is False
    assert "data_agent_stage" not in empty_result.update

    health_message = ToolMessage(
        content='{"ok": true, "result": {"healthy": true}}',
        tool_call_id="call-1",
        name="tablerag_tablerag_validate_index",
    )
    health_result = middleware.wrap_tool_call(
        _tool_request("tablerag_tablerag_validate_index", state=_query_context_state()),
        MagicMock(return_value=health_message),
    )
    assert health_result is health_message


def test_retrieval_state_keeps_success_when_parallel_route_is_empty() -> None:
    """校验并行检索中的空结果不会覆盖同轮成功召回。

    Args:
        无。

    Return:
        None。
    """
    success = {
        "ok": True,
        "tool_name": "tablerag_tablerag_search_columns",
        "query": "指标",
        "content_sha256": "success",
        "result_preview": "columns",
    }
    empty = {
        "ok": False,
        "tool_name": "tablerag_tablerag_search_evidences",
        "query": "指标",
        "content_sha256": "empty",
        "result_preview": "[]",
    }

    assert merge_retrieval_context(success, empty) is success
    assert merge_retrieval_context(success, None) is None


def test_orchestration_blocks_execution_without_matching_validation() -> None:
    """校验 SQL 执行前必须有同一 SQL 的成功校验状态。

    Args:
        无。

    Return:
        None。
    """
    middleware = DataAgentOrchestrationMiddleware()
    handler = MagicMock()
    state = _query_context_state(
        data_retrieval_context={"ok": True},
        data_sql_validation={"valid": True, "sql_sha256": "different"},
    )

    result = middleware.wrap_tool_call(_tool_request("data_execute_sql", state=state), handler)

    handler.assert_not_called()
    assert result.status == "error"
    assert "data_validate_sql" in result.content


def test_orchestration_requires_chart_tool_after_execution_for_chart_intent() -> None:
    """校验图表意图在 SQL 成功后会收到明确的下一步工具约束。

    Args:
        无。

    Return:
        None。
    """
    middleware = DataAgentOrchestrationMiddleware()
    message = middleware._message(
        {
            "data_query_labels": {"intent": "chart", "labels": []},
            "data_sql_validation": {"valid": True, "sql_sha256": "same"},
            "data_sql_execution": {"ok": True, "sql_sha256": "same"},
            "data_chart_spec": None,
        }
    )

    assert "下一步必须调用 `data_build_chart_spec`" in message.content
    assert "不得继续检索、改写或重新校验 SQL" in message.content


def test_orchestration_blocks_unapproved_generic_subagent() -> None:
    """校验 DataAgent 默认禁止通用 task 子代理绕过工具边界。

    Args:
        无。

    Return:
        None。
    """
    middleware = DataAgentOrchestrationMiddleware()
    handler = MagicMock()

    result = middleware.wrap_tool_call(
        _tool_request(
            "task",
            args={
                "description": "检索表结构",
                "prompt": "查找相关表",
                "subagent_type": "general-purpose",
            },
        ),
        handler,
    )

    handler.assert_not_called()
    assert result.status == "error"
    assert "子代理门禁" in result.content


def test_orchestration_requires_retrieval_before_clarification() -> None:
    """校验简短业务指标也必须先经过 TableRAG，再决定是否追问。

    Args:
        无。

    Return:
        None。
    """
    middleware = DataAgentOrchestrationMiddleware()
    handler = MagicMock()

    result = middleware.wrap_tool_call(
        _tool_request(
            "ask_clarification",
            state=_query_context_state(),
            args={"question": "请补充时间范围"},
        ),
        handler,
    )

    handler.assert_not_called()
    assert result.status == "error"
    assert "必须先调用 TableRAG" in result.content


def test_orchestration_caps_sql_execution_attempts_per_turn() -> None:
    """校验单轮 SQL 执行达到预算后会强制收敛到最终答案。

    Args:
        无。

    Return:
        None。
    """
    sql = "SELECT 1"
    state = {
        "messages": [
            HumanMessage(content="测试"),
            ToolMessage(content="{}", tool_call_id="exec-1", name="data_execute_sql"),
            ToolMessage(content="{}", tool_call_id="exec-2", name="data_execute_sql"),
        ],
        "data_query_context": _query_context_state()["data_query_context"],
        "data_retrieval_context": {"ok": True},
        "data_sql_validation": {"valid": True, "sql_sha256": sql_sha256(sql)},
    }
    middleware = DataAgentOrchestrationMiddleware(max_sql_execution_calls=2)
    handler = MagicMock()

    result = middleware.wrap_tool_call(
        _tool_request("data_execute_sql", state=state, args={"sql": sql}),
        handler,
    )

    handler.assert_not_called()
    assert result.status == "error"
    assert "执行次数已达上限" in result.content


def test_orchestration_blocks_new_validation_after_execution_budget() -> None:
    """校验执行预算耗尽后不会用新校验覆盖已有执行状态。

    Args:
        无。

    Return:
        None。
    """
    state = {
        "messages": [
            HumanMessage(content="测试"),
            ToolMessage(content="{}", tool_call_id="exec-1", name="data_execute_sql"),
            ToolMessage(content="{}", tool_call_id="exec-2", name="data_execute_sql"),
        ],
        "data_query_context": _query_context_state()["data_query_context"],
        "data_retrieval_context": {"ok": True},
        "data_sql_validation": {"valid": True, "sql_sha256": "same"},
        "data_sql_execution": {"ok": True, "sql_sha256": "same"},
    }
    middleware = DataAgentOrchestrationMiddleware(max_sql_execution_calls=2)
    handler = MagicMock()

    result = middleware.wrap_tool_call(
        _tool_request("data_validate_sql", state=state, args={"sql": "SELECT 2"}),
        handler,
    )

    handler.assert_not_called()
    assert result.status == "error"
    assert "不再允许校验新 SQL" in result.content


def test_execution_stage_requires_validation_and_execution_digest_match() -> None:
    """校验旧执行结果不能替代最新 SQL 校验对应的执行阶段。

    Args:
        无。

    Return:
        None。
    """
    middleware = DataAgentOrchestrationMiddleware()

    assert (
        middleware._execution_completed(
            {
                "data_sql_validation": {"valid": True, "sql_sha256": "new"},
                "data_sql_execution": {"ok": True, "sql_sha256": "old"},
            }
        )
        is False
    )
    assert (
        middleware._execution_completed(
            {
                "data_sql_validation": {"valid": True, "sql_sha256": "same"},
                "data_sql_execution": {"ok": True, "sql_sha256": "same"},
            }
        )
        is True
    )


def test_orchestration_serializes_parallel_tablerag_calls_per_thread() -> None:
    """校验同一 thread_id 的并行 TableRAG 调用会被串行化。

    Args:
        无。

    Return:
        None。
    """
    middleware = DataAgentOrchestrationMiddleware()
    active = 0
    max_active = 0
    guard = threading.Lock()

    def handler(request):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return ToolMessage(
            content='{"ok": true, "result": [{"table_name": "orders"}]}',
            tool_call_id=str(request.tool_call["id"]),
            name=str(request.tool_call["name"]),
        )

    requests = [
        _tool_request(
            "tablerag_tablerag_search_tables",
            state=_query_context_state(),
            args={"query": f"query-{index}"},
        )
        for index in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda request: middleware.wrap_tool_call(request, handler), requests))

    assert len(results) == 2
    assert max_active == 1


def test_async_tablerag_lock_wait_is_cancellation_safe() -> None:
    """校验异步等待 TableRAG 串行锁被取消后不会遗留永久持锁。

    Args:
        无。

    Return:
        None。
    """

    async def run() -> None:
        middleware = DataAgentOrchestrationMiddleware()
        request = _tool_request(
            "tablerag_tablerag_search_tables",
            state=_query_context_state(),
            args={"query": "指标"},
        )
        lock = middleware._table_rag_lock(request)
        lock.acquire()

        async def handler(tool_request):
            return ToolMessage(
                content='{"ok": true, "result": [{"table_name": "orders"}]}',
                tool_call_id=str(tool_request.tool_call["id"]),
                name=str(tool_request.tool_call["name"]),
            )

        task = asyncio.create_task(middleware.awrap_tool_call(request, handler))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        lock.release()

        result = await asyncio.wait_for(middleware.awrap_tool_call(request, handler), timeout=1)
        assert result is not None
        assert lock.locked() is False

    asyncio.run(run())


def test_stream_values_output_reports_stage_and_execution(capsys) -> None:
    """校验控制台脚本会打印 values 模式的关键阶段状态。

    Args:
        capsys: pytest 输出捕获 fixture。

    Return:
        None。
    """
    module = _load_stream_script()
    observed: dict[str, object] = {}

    module._print_values_event(
        {
            "data_agent_stage": "sql_executed",
            "data_query_labels": {
                "intent": "aggregation",
                "labels": [{"label": "指标", "value": "病例数", "source": "database", "evidence": "metric_name=病例数"}],
            },
            "data_sql_execution": {
                "ok": True,
                "sql_sha256": "abc",
                "row_count": 2,
                "truncated": False,
                "elapsed_ms": 12,
            },
        },
        observed,
    )

    output = capsys.readouterr().out
    assert "[DataAgent:Stage] sql_executed" in output
    assert "[DataAgent:QueryLabels] intent=aggregation" in output
    assert "[DataAgent:SQLExecution] ok=True rows=2" in output


def test_stream_custom_output_reports_query_labels(capsys) -> None:
    """校验控制台脚本会单独打印标签 custom stream 事件。

    Args:
        capsys: pytest 输出捕获 fixture。

    Return:
        None。
    """
    module = _load_stream_script()

    module._print_custom_event(
        {
            "type": "data_query_labels",
            "labels": {
                "intent": "trend",
                "labels": [{"label": "时间粒度", "value": "按月", "source": "derived"}],
            },
        }
    )

    assert "[DataAgent:QueryLabels]" in capsys.readouterr().out


def test_stream_log_path_uses_timestamped_filename(tmp_path) -> None:
    """校验 log.txt 模板会生成带毫秒时间戳的日志文件名。

    Args:
        tmp_path: pytest 临时目录 fixture。

    Return:
        None。
    """
    module = _load_stream_script()
    fixed_now = module.datetime(2026, 7, 10, 9, 8, 7, 654321)

    log_file = module._build_log_file_path(tmp_path / "log.txt", now=fixed_now)

    assert log_file == tmp_path / "log_20260710_090807_654.txt"
    assert log_file.parent.is_dir()


def test_stream_log_records_runtime_variables_and_redacts_credentials(tmp_path, monkeypatch) -> None:
    """校验日志头记录运行变量、流式文本，并脱敏数据库凭据。

    Args:
        tmp_path: pytest 临时目录 fixture。
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    module = _load_stream_script()
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://index-user:index-pass@db.internal:55433/index_db")
    monkeypatch.setenv("DATA_AGENT_MYSQL_DSN", "mysql+pymysql://reporter:test%40password@db.internal:3308/analytics")
    monkeypatch.setenv("DATA_AGENT_MYSQL_PASSWORD", "test@password")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-log-secret")
    args = module._build_parser().parse_args(
        [
            "测试问题",
            "--log-path",
            str(tmp_path / "log.txt"),
            "--skip-db-preflight",
        ]
    )
    output = module._configure_logging(args.log_path)
    log_file = output.log_file

    try:
        module._log_runtime_context(output, args=args, repo_root=REPO_ROOT)
        output.stream_text("第一行\n第二行")
        module.logging.getLogger("deerflow.test").info("dependency-log-event dsn=mysql+pymysql://reporter:test%40password@db.internal/analytics password=test@password provider-key=sk-test-log-secret")
        try:
            raise RuntimeError("dependency failure password=test@password")
        except RuntimeError:
            module.logging.getLogger("deerflow.test").exception("dependency-log-exception")
        output.info("stream.completed")
    finally:
        output.close()

    content = log_file.read_text(encoding="utf-8")
    assert " | INFO | " in content
    assert "env.TABLERAG_MCP_INDEX_DSN=postgresql://index-user:***@db.internal:55433/index_db" in content
    assert "env.DATA_AGENT_MYSQL_DSN=mysql+pymysql://reporter:***@db.internal:3308/analytics" in content
    assert "env.DATA_AGENT_MYSQL_PASSWORD=<SET:REDACTED>" in content
    assert "index-user:index-pass@" not in content
    assert "test%40password" not in content
    assert "test@password" not in content
    assert "sk-test-log-secret" not in content
    assert "[stream.ai] 第一行" in content
    assert "[stream.ai] 第二行" in content
    assert ("deerflow.test | dependency-log-event dsn=mysql+pymysql://reporter:***@db.internal/analytics password=*** provider-key=***") in content
    assert "dependency-log-exception" in content
    assert "RuntimeError: dependency failure password=***" in content


def test_stream_parser_error_is_written_to_log(tmp_path) -> None:
    """校验参数校验失败会先写入日志，再按 argparse 标准退出。

    Args:
        tmp_path: pytest 临时目录 fixture。

    Return:
        None。
    """
    module = _load_stream_script()
    parser = module._build_parser()
    output = module._configure_logging(tmp_path / "log.txt")
    log_file = output.log_file

    try:
        with pytest.raises(SystemExit) as exc_info:
            module._abort_with_parser_error(parser, output, "测试参数错误")
        assert exc_info.value.code == 2
    finally:
        output.close()

    content = log_file.read_text(encoding="utf-8")
    assert " | ERROR | " in content
    assert "runtime.invalid_arguments message=测试参数错误" in content


def test_stream_dataset_reader_supports_gb18030(tmp_path) -> None:
    """校验控制台脚本可读取 GB18030 指标数据集并去重。

    Args:
        tmp_path: pytest 临时目录 fixture。

    Return:
        None。
    """
    module = _load_stream_script()
    dataset = tmp_path / "metrics.csv"
    dataset.write_bytes("指标名称,参数,SQL\r\n指标甲,,SELECT 1\r\n指标甲,,SELECT 1\r\n指标乙,,SELECT 2\r\n".encode("gb18030"))

    questions = module._load_dataset_questions(
        dataset,
        sample_count=2,
        question_column="指标名称",
    )

    assert questions == ["指标甲", "指标乙"]


def test_stream_dataset_reader_rejects_unbounded_sample_count(tmp_path) -> None:
    """校验 CSV 模式限制单次触发的模型任务数量。

    Args:
        tmp_path: pytest 临时目录 fixture。

    Return:
        None。
    """
    module = _load_stream_script()
    dataset = tmp_path / "metrics.csv"
    dataset.write_text("指标名称\n指标甲\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="1 到 100"):
        module._load_dataset_questions(
            dataset,
            sample_count=101,
            question_column="指标名称",
        )


def test_stream_dataset_path_prefers_existing_working_directory_file(tmp_path, monkeypatch) -> None:
    """校验移动后的脚本可以解析当前目录中的相对 CSV 路径。

    Args:
        tmp_path: pytest 临时目录 fixture。
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    module = _load_stream_script()
    dataset = tmp_path / "metrics.csv"
    dataset.write_text("指标名称\n指标甲\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    resolved = module._resolve_dataset_path(Path("metrics.csv"), repo_root=REPO_ROOT)

    assert resolved == dataset.resolve()


def test_build_data_agent_uses_create_deerflow_agent(monkeypatch) -> None:
    """校验 DataAgent 图最终通过 create_deerflow_agent 创建。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    from agents.data_agent import agent as data_agent_module

    from deerflow.tools.builtins import tool_search

    calls: dict[str, object] = {}

    def fake_create_deerflow_agent(**kwargs):
        calls.update(kwargs)
        return "compiled-data-agent"

    fake_app_config = SimpleNamespace(
        models=[SimpleNamespace(name="mock-model")],
        skills=SimpleNamespace(deferred_discovery=False, container_path="/mnt/skills"),
        tool_search=SimpleNamespace(enabled=False),
        get_model_config=lambda name: SimpleNamespace(supports_thinking=True),
    )

    monkeypatch.setattr(data_agent_module, "_load_optional_agent_config", lambda agent_name, user_id=None: None)
    monkeypatch.setattr(data_agent_module, "_resolve_model_name", lambda requested_model_name=None, app_config=None: "mock-model")
    monkeypatch.setattr(data_agent_module, "_load_enabled_skills_for_tool_policy", lambda available_skills, app_config, user_id=None: [])
    monkeypatch.setattr(data_agent_module, "build_skill_search_setup", lambda *args, **kwargs: SimpleNamespace(describe_skill_tool=None, skill_names=frozenset()))
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [tag_mcp_tool(_fake_tool("tablerag_tablerag_retrieve"))])
    monkeypatch.setattr(tool_search, "assemble_deferred_tools", lambda tools, enabled: (tools, SimpleNamespace(deferred_names=frozenset())))
    monkeypatch.setattr(tool_search, "get_mcp_routing_hints_prompt_section", lambda tools, deferred_names: "")
    prompt_kwargs: dict[str, object] = {}

    def fake_apply_prompt_template(**kwargs):
        prompt_kwargs.update(kwargs)
        return "lead-prompt"

    monkeypatch.setattr(data_agent_module, "apply_prompt_template", fake_apply_prompt_template)
    monkeypatch.setattr(data_agent_module, "create_chat_model", lambda **kwargs: MagicMock(name="model"))
    monkeypatch.setattr(data_agent_module, "build_data_middlewares", lambda *args, **kwargs: [DataAgentTurnResetMiddleware()])
    monkeypatch.setattr(data_agent_module, "create_deerflow_agent", fake_create_deerflow_agent)

    result = build_data_agent(
        {
            "configurable": {
                "thread_id": "data-agent-test",
                "thinking_enabled": True,
                "subagent_enabled": False,
            }
        },
        app_config=fake_app_config,
    )

    assert result == "compiled-data-agent"
    assert calls["name"] == DATA_AGENT_NAME
    assert calls["state_schema"] is DataAgentState
    assert calls["system_prompt"].startswith("lead-prompt")
    assert isinstance(calls["middleware"][0], DataAgentTurnResetMiddleware)
    assert prompt_kwargs["agent_name"] == DATA_AGENT_NAME
    assert {
        ENTITY_EXTRACT_TOOL_NAME,
        PUBLISH_QUERY_LABELS_TOOL_NAME,
        "data_validate_sql",
        "data_execute_sql",
        "data_build_chart_spec",
    } <= {tool.name for tool in calls["tools"]}
