"""DataAgent TableRAG、标签快照和确认策略测试。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest

from deerflow.agents.middlewares.query_labels_middleware import QueryLabelsMiddleware
from deerflow.agents.service_agent.approval_middleware import QueryIntentApprovalMiddleware
from deerflow.agents.service_agent.binding import resolve_data_source_binding
from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig
from deerflow.agents.service_agent.registry import DataAgentServiceAbility
from deerflow.agents.service_agent.sql_executor import execute_sql, validate_sql
from deerflow.agents.service_agent.sql_stage_middleware import SqlStageMiddleware
from deerflow.agents.service_agent.sql_tools import build_sql_tools
from deerflow.agents.service_agent.sqlrag_contract import is_sqlrag_retrieval_tool_name
from deerflow.agents.service_agent.state import (
    build_query_approval_request,
    build_query_label_snapshot,
    build_query_review_items,
    build_retrieval_context,
    decide_query_approval,
    merge_retrieval_contexts,
)
from deerflow.agents.service_agent.table_rag_middleware import TableRagStageMiddleware
from deerflow.agents.service_agent.turn_context import current_visible_turn_id
from deerflow.agents.thread_state import ThreadState, merge_service_states
from deerflow.subagents.status_contract import make_subagent_additional_kwargs, read_subagent_result_metadata
from deerflow.tools.builtins.ask_intent_approval_tool import ask_intent_approval_tool
from deerflow.tools.builtins.query_labels_tool import publish_query_labels_tool
from deerflow.tools.builtins.task_tool import _build_data_query_sql_result_from_steps


class _DataQueryFlowModel(FakeMessagesListChatModel):
    """支持绑定工具的确定性 DataAgent 生产闭环测试模型。"""

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Runnable:
        """返回预设响应模型，测试不调用真实 LLM。"""
        return self


def _config(confirmation_mode: str = "on_ambiguity") -> DataQueryServiceAbilityConfig:
    """构造 DataAgent 查询能力配置。"""
    return DataQueryServiceAbilityConfig.model_validate(
        {
            "type": "data_query",
            "version": 1,
            "enable_sql_rag": True,
            "table_rag_config": "tabelrag.yaml",
            "data_source_id": "sales-pg",
            "confirmation_mode": confirmation_mode,
            "min_auto_confidence": 0.85,
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


def _mysql_config() -> DataQueryServiceAbilityConfig:
    """构造真实拓扑使用的 MySQL 业务执行配置。"""
    raw = _config().model_dump()
    raw["data_source_id"] = "sales-mysql"
    raw["source_binding_mode"] = "logical_data_source"
    raw["sql_execution"].update(
        {
            "database_type": "mysql",
            "dsn_env": "DATA_AGENT_MYSQL_DSN",
            "allowed_schemas": ["text2sql"],
        }
    )
    return DataQueryServiceAbilityConfig.model_validate(raw)


def _retrieval_payload() -> dict:
    """构造 TableRAG 成功响应。"""
    return {
        "ok": True,
        "operation": "hybrid-search",
        "result": {
            "query": "查询华东销售额最高的商品",
            "evidences": [
                {
                    "evidence_content": "销售额口径为 SUM(order_amount)",
                    "evidence_type": "metric_rule",
                    "score": 0.93,
                    "metadata": {"domain": "sales"},
                }
            ],
            "tables": [{"table_name": "orders", "score": 0.91}],
            "columns": [
                {"table_name": "orders", "column_name": "order_amount", "score": 0.89},
                {"table_name": "orders", "column_name": "region", "score": 0.88},
            ],
            "values": [{"table_name": "orders", "column_name": "region", "value": "华东", "score": 0.96}],
            "join_graphs": [],
            "metadata": {"candidate_table_count": 1},
        },
    }


def _binding() -> dict:
    """构造无 Secret 的同源数据库绑定。"""
    return {
        "version": 1,
        "data_source_id": "sales-pg",
        "database_type": "postgresql",
        "table_rag_config_ref": "tabelrag.yaml",
        "retrieval_target_fingerprint": "sha256:target",
        "execution_secret_ref": "DATA_AGENT_SQL_DSN",
        "execution_target_fingerprint": "sha256:target",
        "binding_fingerprint": "sha256:binding",
        "allowed_schemas": ["public"],
    }


def _mysql_binding(binding_fingerprint: str = "sha256:mysql-binding") -> dict:
    """构造 PostgreSQL 索引与 MySQL 执行源的逻辑绑定。"""
    return {
        "version": 1,
        "data_source_id": "sales-mysql",
        "database_type": "mysql",
        "source_binding_mode": "logical_data_source",
        "table_rag_config_ref": "tabelrag.yaml",
        "retrieval_target_fingerprint": "sha256:postgres-index",
        "execution_secret_ref": "DATA_AGENT_MYSQL_DSN",
        "execution_target_fingerprint": "sha256:mysql-source",
        "binding_fingerprint": binding_fingerprint,
        "allowed_schemas": ["text2sql"],
    }


def test_build_retrieval_context_registers_stable_refs() -> None:
    """服务端必须为 TableRAG 对象生成稳定 ref 和检索摘要。"""
    first = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    second = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )

    assert first["ok"] is True
    assert first["retrieval_digest"] == second["retrieval_digest"]
    assert first["evidences"][0]["ref"].startswith("evidence:sha256:")
    assert first["tables"][0]["ref"].startswith("table:sha256:")
    assert first["columns"][0]["ref"].startswith("column:sha256:")
    assert first["values"][0]["ref"].startswith("value:sha256:")
    assert set(first["registry"]) == {
        first["evidences"][0]["ref"],
        first["tables"][0]["ref"],
        first["columns"][0]["ref"],
        first["columns"][1]["ref"],
        first["values"][0]["ref"],
    }


def test_build_retrieval_context_rejects_failed_or_empty_result() -> None:
    """失败或无候选对象的检索不能成为 SQL Evidence。"""
    with pytest.raises(ValueError):
        build_retrieval_context(
            {"ok": False, "error": {"message": "boom"}},
            tool_name="sqlrag_retrieve",
            turn_id="turn-1",
            data_source_id="sales-pg",
            binding=_binding(),
        )
    with pytest.raises(ValueError):
        build_retrieval_context(
            {"ok": True, "result": {"evidences": [], "tables": [], "columns": [], "values": [], "join_graphs": []}},
            tool_name="sqlrag_retrieve",
            turn_id="turn-1",
            data_source_id="sales-pg",
            binding=_binding(),
        )


def test_supplemental_retrieval_merges_registry_and_changes_digest() -> None:
    """补充检索不能覆盖首轮 Evidence，且必须产生新的快照摘要。"""
    first = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    payload = {
        "ok": True,
        "operation": "search-columns",
        "result": [{"table_name": "orders", "column_name": "id", "score": 0.87}],
    }
    second = build_retrieval_context(
        payload,
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
        request_args={"operation": "search-columns", "queries": ["订单日期"]},
    )

    merged = merge_retrieval_contexts(first, second)

    assert len(merged["registry"]) > len(first["registry"])
    assert first["retrieval_digest"] != merged["retrieval_digest"]
    assert merged["queries"] == [first["query"]]
    assert merged["keyword_queries"] == ["订单日期"]
    assert merged["operations"] == ["hybrid-search", "search-columns"]


def test_single_route_column_operation_uses_explicit_mapping() -> None:
    """统一工具必须按 operation 归档单路字段结果。"""
    retrieval = build_retrieval_context(
        {
            "ok": True,
            "operation": "search-columns",
            "result": [{"table_name": "femalediagnosticinfo", "column_name": "FemaleFactor"}],
        },
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
        request_args={"operation": "search-columns", "queries": ["女性因素"]},
    )

    assert retrieval["tables"] == []
    assert retrieval["columns"][0]["column_name"] == "FemaleFactor"
    assert retrieval["operation"] == "search-columns"
    assert retrieval["keyword_queries"] == ["女性因素"]
    assert next(iter(retrieval["registry"].values()))["kind"] == "column"


def test_data_agent_accepts_only_exact_sqlrag_tool_name() -> None:
    """DataAgent 不再兼容旧 TableRAG 工具名或 MCP Server 冗余前缀。"""
    assert is_sqlrag_retrieval_tool_name("sqlrag_retrieve") is True
    assert is_sqlrag_retrieval_tool_name("tablerag_sqlrag_retrieve") is False
    assert is_sqlrag_retrieval_tool_name("tablerag_retrieve") is False


def test_query_label_snapshot_binds_database_labels_to_registry() -> None:
    """数据库标签只能引用当前检索 registry 中的 ref。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    evidence_ref = retrieval["evidences"][0]["ref"]

    snapshot = build_query_label_snapshot(
        turn_id="turn-1",
        data_source_id="sales-pg",
        ability_version=1,
        retrieval=retrieval,
        intent="ranking",
        summary="查询华东销售额最高的商品",
        confidence=0.92,
        ambiguities=[],
        labels=[
            {
                "label": "指标",
                "value": "销售额",
                "source": "database",
                "normalized": "SUM(order_amount)",
                "evidence_refs": [evidence_ref],
            }
        ],
    )

    assert snapshot["snapshot_id"].startswith("sha256:")
    assert snapshot["retrieval_digest"] == retrieval["retrieval_digest"]
    assert snapshot["labels"][0]["evidence_refs"] == [evidence_ref]

    with pytest.raises(ValueError, match="Evidence"):
        build_query_label_snapshot(
            turn_id="turn-1",
            data_source_id="sales-pg",
            ability_version=1,
            retrieval=retrieval,
            intent="ranking",
            summary=None,
            confidence=0.92,
            ambiguities=[],
            labels=[
                {
                    "label": "指标",
                    "value": "销售额",
                    "source": "database",
                    "evidence_refs": ["evidence:sha256:forged"],
                }
            ],
        )


@pytest.mark.parametrize(
    ("mode", "confidence", "ambiguities", "expected_required"),
    [
        ("auto", 0.95, [], False),
        ("auto", None, [], False),
        ("on_ambiguity", 0.95, [], False),
        ("on_ambiguity", 0.80, [], True),
        ("on_ambiguity", 0.95, ["时间范围不明确"], True),
        ("always", 0.99, [], True),
    ],
)
def test_confirmation_policy_is_fail_closed(
    mode: str,
    confidence: float | None,
    ambiguities: list[str],
    expected_required: bool,
) -> None:
    """确认策略只表达是否需要调用意图审批工具，不再直接伪造人工审批结果。"""
    decision = decide_query_approval(
        _config(mode),
        {
            "snapshot_id": "sha256:snapshot",
            "confidence": confidence,
            "ambiguities": ambiguities,
            "labels": [{"label": "指标", "value": "销售额", "source": "user"}],
            "retrieval_digest": "sha256:retrieval",
            "binding_fingerprint": "sha256:target",
            "constraints_complete": True,
            "ambiguities_declared": True,
        },
    )

    assert decision["required"] is expected_required
    assert decision["mode"] == mode
    assert decision["snapshot_id"] == "sha256:snapshot"


def test_approval_request_uses_data_agent_intent_approval_source() -> None:
    """确认请求携带 snapshot，并用 DataAgent 专属 source 区分通用 clarification。"""
    request = build_query_approval_request(
        {
            "snapshot_id": "sha256:snapshot",
            "summary": "查询华东销售额最高的商品",
            "ambiguities": ["是否排除退款"],
        },
        tool_call_id="labels-call-1",
    )

    assert request["source"] == "ask_intent_approval"
    assert request["request_id"].startswith("data-query:")
    assert request["snapshot_id"] == "sha256:snapshot"
    assert [item["id"] for item in request["options"]] == ["execute", "sql_only", "cancel"]


def test_query_review_items_have_stable_ids_and_are_bound_to_request() -> None:
    """每个 ambiguity 都生成稳定 ID，并随确认请求绑定当前 snapshot。"""
    snapshot = {
        "snapshot_id": "sha256:snapshot",
        "summary": "查询销售额",
        "ambiguities": ["时间范围不明确", "是否排除退款"],
    }
    first = build_query_review_items(snapshot)
    second = build_query_review_items(snapshot)
    request = build_query_approval_request(snapshot, tool_call_id="labels-1")

    assert first == second
    assert len(first) == 2
    assert request["review_items"] == first
    assert all(item["status"] == "pending" for item in first)


def test_ask_intent_approval_tool_creates_visible_approval_card() -> None:
    """模型调用意图审批工具时，必须生成 ToolMessage 审批卡并暂停图执行。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-approval-card",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    snapshot = build_query_label_snapshot(
        turn_id="turn-approval-card",
        data_source_id="sales-pg",
        ability_version=1,
        retrieval=retrieval,
        intent="ranking",
        summary="查询华东销售额最高的商品",
        ambiguities=["是否排除退款"],
        labels=[
            {
                "label": "指标",
                "value": "销售额",
                "source": "database",
                "evidence_refs": [retrieval["evidences"][0]["ref"]],
            }
        ],
    )
    state = {
        "messages": [HumanMessage(id="turn-approval-card", content="查询华东销售额")],
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-approval-card",
                "snapshot_id": snapshot["snapshot_id"],
                "stage": "labels_published",
                "data_source_id": "sales-pg",
                "payload": {
                    "retrieval": retrieval,
                    "labels": snapshot,
                    "approval_policy": decide_query_approval(_config(), snapshot),
                    "review_items": build_query_review_items(snapshot),
                },
            }
        ],
    }
    request = ToolCallRequest(
        tool_call={"name": "ask_intent_approval", "id": "approval-call-1", "args": {}},
        tool=None,
        state=state,
        runtime=MagicMock(context={"thread_id": "thread-1"}),
    )

    result = QueryIntentApprovalMiddleware(_config()).wrap_tool_call(
        request,
        lambda _request: pytest.fail("审批工具必须由 middleware 拦截"),
    )

    assert result.goto == END
    message = result.update["messages"][0]
    service_state = result.update["service_states"][0]
    assert message.name == "ask_intent_approval"
    assert message.artifact["kind"] == "data_query_labels"
    assert message.artifact["human_input"]["source"] == "ask_intent_approval"
    assert message.artifact["approval"]["status"] == "awaiting_confirmation"
    assert service_state["stage"] == "awaiting_confirmation"
    assert service_state["payload"]["approval_request"]["request_id"].startswith("data-query:")


def _tool_request(name: str, *, state: dict | None = None) -> ToolCallRequest:
    """构造 DataAgent 业务 middleware 工具请求。"""
    runtime = MagicMock()
    runtime.context = {"thread_id": "thread-1"}
    return ToolCallRequest(
        tool_call={"name": name, "args": {"query": "查询华东销售额"}, "id": "tool-call-1"},
        tool=None,
        state=state or {"messages": [HumanMessage(id="turn-1", content="查询华东销售额")]},
        runtime=runtime,
    )


def test_service_state_reducer_allows_new_turn_retrieval_without_forced_reset() -> None:
    """新可见用户问题可以由 TableRAG 检索直接创建新快照，不需要先写 idle 重置。"""
    stale_state = [
        {
            "service_name": "data_query",
            "version": 1,
            "turn_id": "turn-1",
            "stage": "needs_refinement",
            "payload": {"retrieval": {"ok": False}},
        }
    ]
    retrieving = [
        {
            "service_name": "data_query",
            "version": 1,
            "turn_id": "turn-2",
            "stage": "retrieving",
            "payload": {"retrieval": {"ok": True}},
        }
    ]

    merged = merge_service_states(stale_state, retrieving)

    assert merged[0]["turn_id"] == "turn-2"
    assert merged[0]["stage"] == "retrieving"


def test_current_visible_turn_id_ignores_hidden_intent_approval_response() -> None:
    """human-input 隐藏确认属于工具回复，不得被识别为新的业务用户问题。"""
    state = {
        "messages": [
            HumanMessage(id="turn-1", content="查询销售额"),
            HumanMessage(
                id="hidden-confirmation",
                content="已确认",
                additional_kwargs={
                    "hide_from_ui": True,
                        "human_input_response": {
                            "version": 1,
                            "kind": "human_input_response",
                            "source": "ask_intent_approval",
                            "request_id": "data-query:req-1",
                            "response_kind": "text",
                            "value": "确认执行",
                    },
                },
            ),
        ],
    }

    assert current_visible_turn_id(state) == "turn-1"


def test_data_agent_starts_retrieval_from_new_visible_turn_after_stale_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """正式 DataAgent 图在旧轮次快照存在时仍能从新用户消息开始检索。"""
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", "postgresql://readonly:secret@db.local:5432/sales")
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@db.local:5432/sales")

    @tool("sqlrag_retrieve")
    def fake_tablerag(operation: str = "hybrid-search", query: str | None = None, queries: list[str] | None = None) -> str:
        """返回固定的 TableRAG 检索结果。"""
        return json.dumps(_retrieval_payload(), ensure_ascii=False)

    model = _DataQueryFlowModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sqlrag_retrieve",
                        "id": "rag-new-turn",
                        "args": {"operation": "hybrid-search", "query": "重新查询"},
                    }
                ],
            ),
            AIMessage(content="已完成检索"),
        ]
    )
    ability = DataAgentServiceAbility(_config())
    graph = create_agent(
        model=model,
        tools=[fake_tablerag],
        middleware=ability.build_middlewares(),
        state_schema=ThreadState,
    )

    final_state = graph.invoke(
        {
            "messages": [
                HumanMessage(id="turn-old", content="上一轮查询"),
                HumanMessage(id="turn-new", content="重新查询"),
            ],
            "service_states": [
                {
                    "service_name": "data_query",
                    "version": 1,
                    "turn_id": "turn-old",
                    "stage": "needs_refinement",
                    "payload": {"retrieval": {"ok": False}},
                }
            ],
        }
    )

    active = final_state["service_states"][0]
    assert active["turn_id"] == "turn-new"
    assert active["stage"] == "retrieving"


def test_table_rag_middleware_registers_successful_result_in_service_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """TableRAG 正式工具调用成功后只写 service_states，不写顶层业务字段。"""
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", "postgresql://readonly:secret@db.local:5432/sales")
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@db.local:5432/sales")
    middleware = TableRagStageMiddleware(_config())
    message = ToolMessage(
        content=json.dumps(_retrieval_payload(), ensure_ascii=False),
        tool_call_id="tool-call-1",
        name="sqlrag_retrieve",
    )

    result = middleware.wrap_tool_call(_tool_request("sqlrag_retrieve"), lambda _request: message)

    assert result.update["messages"] == [message]
    assert "data_retrieval_context" not in result.update
    service_state = result.update["service_states"][0]
    assert service_state["stage"] == "retrieving"
    assert service_state["turn_id"] == "turn-1"
    assert service_state["data_source_id"] == "sales-pg"
    assert service_state["payload"]["retrieval"]["registry"]


def test_table_rag_middleware_serializes_retrieval_calls_per_model_response() -> None:
    """同一 AIMessage 只允许一个 TableRAG 调用，补充检索必须在后续轮次串行合并。"""
    state = {
        "messages": [
            AIMessage(
                id="ai-rag-parallel",
                content="",
                tool_calls=[
                    {"name": "sqlrag_retrieve", "id": "rag-1", "args": {"operation": "hybrid-search", "query": "销售额"}},
                    {"name": "sqlrag_retrieve", "id": "rag-2", "args": {"operation": "search-columns", "queries": ["地区"]}},
                    {"name": "read_file", "id": "read-1", "args": {"file_path": "notes.md"}},
                ],
            )
        ]
    }

    update = TableRagStageMiddleware(_config()).after_model(state, MagicMock())

    assert [item["id"] for item in update["messages"][0].tool_calls] == ["rag-1", "read-1"]


def test_table_rag_middleware_marks_empty_result_as_needs_refinement(monkeypatch: pytest.MonkeyPatch) -> None:
    """空检索进入 needs_refinement，禁止伪造标签或执行 SQL。"""
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", "postgresql://readonly:secret@db.local:5432/sales")
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@db.local:5432/sales")
    middleware = TableRagStageMiddleware(_config())
    message = ToolMessage(
        content=json.dumps({"ok": True, "result": {"evidences": [], "tables": [], "columns": [], "values": [], "join_graphs": []}}),
        tool_call_id="tool-call-1",
        name="sqlrag_retrieve",
    )

    result = middleware.wrap_tool_call(_tool_request("sqlrag_retrieve"), lambda _request: message)

    service_state = result.update["service_states"][0]
    assert service_state["stage"] == "needs_refinement"
    assert service_state["payload"]["retrieval"]["error_code"] == "TABLERAG_EMPTY_OR_FAILED"


def test_table_rag_middleware_preserves_prior_success_when_supplement_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """同轮补充检索为空时必须保留已有 Evidence，只记录失败尝试。"""
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", "postgresql://readonly:secret@db.local:5432/sales")
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@db.local:5432/sales")
    prior = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    state = {
        "messages": [HumanMessage(id="turn-1", content="查询华东销售额")],
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": prior["retrieval_digest"],
                "stage": "retrieving",
                "data_source_id": "sales-pg",
                "payload": {"retrieval": prior, "retrieval_calls": 1},
            }
        ],
    }
    message = ToolMessage(
        content=json.dumps({"ok": True, "result": {"evidences": [], "tables": [], "columns": [], "values": [], "join_graphs": []}}),
        tool_call_id="tool-call-1",
        name="sqlrag_retrieve",
    )

    result = TableRagStageMiddleware(_config()).wrap_tool_call(
        _tool_request("sqlrag_retrieve", state=state),
        lambda _request: message,
    )

    service_state = result.update["service_states"][0]
    assert service_state["stage"] == "retrieving"
    assert service_state["snapshot_id"] == prior["retrieval_digest"]
    assert service_state["payload"]["retrieval"] == prior
    assert service_state["payload"]["retrieval_calls"] == 2
    assert service_state["payload"]["last_retrieval_error"]["error_code"] == "TABLERAG_EMPTY_OR_FAILED"


def test_sql_stage_allows_historical_approved_snapshot_for_regeneration_request() -> None:
    """用户追问重新生成 SQL 时，模型可基于历史已批准快照继续进入 SQL 阶段。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    validation = validate_sql(
        "SELECT orders.region FROM public.orders LIMIT 500",
        config=_config(),
        retrieval=retrieval,
        snapshot_id="sha256:approved",
    )
    state = {
        "messages": [
            HumanMessage(id="turn-1", content="上一轮查询"),
            HumanMessage(id="turn-2", content="重新生成 SQL 并执行结果"),
        ],
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": "sha256:approved",
                "stage": "approved",
                "payload": {
                    "approval": {"status": "approved", "action": "sql_only", "source": "human"},
                    "retrieval": retrieval,
                    "labels": {"intent": "detail", "labels": []},
                },
            }
        ],
    }
    runtime = MagicMock()
    runtime.context = {
        "data_query_service_ability": _config().model_dump(mode="json"),
        "data_query_sql_subagent_allowed": True,
    }
    request = ToolCallRequest(
        tool_call={
            "name": "task",
            "id": "task-stale-snapshot",
            "args": {
                "description": "SQL",
                "prompt": "ignored",
                "subagent_type": "sql-subagent",
            },
        },
        tool=None,
        state=state,
        runtime=runtime,
    )

    def handler(next_request: ToolCallRequest) -> ToolMessage:
        envelope = json.loads(next_request.tool_call["args"]["prompt"])
        assert envelope["snapshot_id"] == "sha256:approved"
        assert envelope["action"] == "sql_only"
        return ToolMessage(
            content=json.dumps(
                {
                    "version": 1,
                    "kind": "data_query_sql_result",
                    "snapshot_id": "sha256:approved",
                    "data_source_id": "sales-pg",
                    "validation": validation,
                    "execution": None,
                },
                ensure_ascii=False,
            ),
            tool_call_id="task-stale-snapshot",
            name="task",
        )

    result = SqlStageMiddleware(_config()).wrap_tool_call(request, handler)

    assert result.update["service_states"][0]["stage"] == "succeeded"
    assert result.update["service_states"][0]["payload"]["approval"]["source"] == "human"


def test_sql_stage_allows_policy_based_execution_from_labels_published_snapshot() -> None:
    """确认策略不要求人工时，labels_published 快照也可以直接进入 SQL 阶段。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-policy",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    snapshot = build_query_label_snapshot(
        turn_id="turn-policy",
        data_source_id="sales-pg",
        ability_version=1,
        retrieval=retrieval,
        intent="ranking",
        summary="查询华东销售额最高的商品",
        ambiguities=[],
        labels=[
            {
                "label": "指标",
                "value": "销售额",
                "source": "database",
                "evidence_refs": [retrieval["evidences"][0]["ref"]],
            }
        ],
    )
    state = {
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-policy",
                "snapshot_id": snapshot["snapshot_id"],
                "stage": "labels_published",
                "payload": {
                    "retrieval": retrieval,
                    "labels": snapshot,
                    "approval_policy": {"version": 1, "snapshot_id": snapshot["snapshot_id"], "required": False, "mode": "auto", "reason": "auto"},
                },
            }
        ]
    }
    request = ToolCallRequest(
        tool_call={
            "name": "task",
            "id": "task-policy",
            "args": {"description": "SQL", "prompt": "ignored", "subagent_type": "sql-subagent"},
        },
        tool=None,
        state=state,
        runtime=MagicMock(context={"thread_id": "thread-1", "data_query_service_ability": _config().model_dump(mode="json"), "data_query_sql_subagent_allowed": True}),
    )
    validation = validate_sql(
        "SELECT orders.region FROM public.orders LIMIT 500",
        config=_config(),
        retrieval=retrieval,
        snapshot_id=snapshot["snapshot_id"],
    )

    def handler(next_request: ToolCallRequest) -> ToolMessage:
        envelope = json.loads(next_request.tool_call["args"]["prompt"])
        assert envelope["action"] == "execute"
        assert envelope["snapshot_id"] == snapshot["snapshot_id"]
        return ToolMessage(
            content=json.dumps(
                {
                    "version": 1,
                    "kind": "data_query_sql_result",
                    "snapshot_id": snapshot["snapshot_id"],
                    "data_source_id": "sales-pg",
                    "validation": validation,
                    "execution": {
                        "version": 1,
                        "ok": True,
                        "snapshot_id": snapshot["snapshot_id"],
                        "validation_digest": validation["validation_digest"],
                        "columns": ["region"],
                        "rows": [],
                        "row_count": 0,
                        "returned_row_count": 0,
                        "truncated": False,
                        "empty": True,
                    },
                },
                ensure_ascii=False,
            ),
            tool_call_id="task-policy",
            name="task",
        )

    result = SqlStageMiddleware(_config()).wrap_tool_call(request, handler)

    assert result.update["service_states"][0]["stage"] == "succeeded"
    assert result.update["service_states"][0]["payload"]["approval"]["source"] == "policy"


def test_query_intent_approval_middleware_accepts_only_matching_hidden_response() -> None:
    """意图审批 middleware 必须校验 source/request_id，且只推进一次授权。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    snapshot = build_query_label_snapshot(
        turn_id="turn-1",
        data_source_id="sales-pg",
        ability_version=1,
        retrieval=retrieval,
        intent="ranking",
        summary="查询华东销售额",
        ambiguities=[],
        labels=[
            {
                "label": "指标",
                "value": "销售额",
                "source": "database",
                "evidence_refs": [retrieval["evidences"][0]["ref"]],
            }
        ],
    )
    payload = {
        "retrieval": retrieval,
        "labels": snapshot,
        "approval": {"version": 1, "status": "awaiting_confirmation", "action": None, "source": None},
        "approval_request": {
            "source": "ask_intent_approval",
            "request_id": "data-query:req-1",
        },
    }
    state = {
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": snapshot["snapshot_id"],
                "stage": "awaiting_confirmation",
                "payload": payload,
            }
        ],
        "messages": [
            HumanMessage(
                content="确认",
                additional_kwargs={
                    "human_input_response": {
                        "version": 1,
                        "kind": "human_input_response",
                        "source": "ask_intent_approval",
                        "request_id": "data-query:req-1",
                        "response_kind": "option",
                        "option_id": "sql_only",
                        "value": "sql_only",
                    }
                },
            )
        ],
    }

    result = QueryIntentApprovalMiddleware(_config()).before_agent(state, MagicMock())

    assert result["service_states"][0]["stage"] == "approved"
    assert result["service_states"][0]["payload"]["approval"]["action"] == "sql_only"
    assert result["messages"][0].name == "ask_intent_approval"
    assert result["messages"][0].artifact["approval"]["status"] == "approved"
    assert result["messages"][0].artifact["approval_result"]["action"] == "sql_only"

    state["messages"][0].additional_kwargs["human_input_response"]["request_id"] = "data-query:stale"
    assert QueryIntentApprovalMiddleware(_config()).before_agent(state, MagicMock()) is None


def test_query_intent_approval_text_revision_invalidates_old_query_snapshot() -> None:
    """用户修改条件后生成新的检索快照，旧 Evidence、标签、授权和 SQL 结果全部失效。"""
    state = {
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": "sha256:old-snapshot",
                "stage": "awaiting_confirmation",
                "payload": {
                    "retrieval": build_retrieval_context(
                        _retrieval_payload(),
                        tool_name="sqlrag_retrieve",
                        turn_id="turn-1",
                        data_source_id="sales-pg",
                        binding=_binding(),
                    ),
                    "labels": {
                        "snapshot_id": "sha256:old-snapshot",
                        "turn_id": "turn-1",
                        "retrieval_digest": "sha256:old-retrieval",
                        "binding_fingerprint": "sha256:target",
                        "intent": "ranking",
                        "labels": [
                            {
                                "label": "指标",
                                "value": "销售额",
                                "source": "user",
                                "evidence_refs": [],
                            }
                        ],
                        "ambiguities": ["是否排除退款"],
                    },
                    "approval": {"version": 1, "status": "awaiting_confirmation", "action": None, "source": None},
                    "approval_request": {"source": "ask_intent_approval", "request_id": "data-query:req-revision"},
                    "sql_result": {"execution": {"ok": True}},
                },
            }
        ],
        "messages": [
            HumanMessage(id="turn-1", content="查询销售额"),
            HumanMessage(
                content="改查去年并排除退款",
                additional_kwargs={
                        "human_input_response": {
                            "version": 1,
                            "kind": "human_input_response",
                            "source": "ask_intent_approval",
                            "request_id": "data-query:req-revision",
                            "response_kind": "text",
                            "value": "改查去年并排除退款",
                    }
                },
            ),
        ],
    }

    result = QueryIntentApprovalMiddleware(_config()).before_agent(state, MagicMock())

    service_state = result["service_states"][0]
    assert service_state["stage"] == "retrieving"
    assert service_state["snapshot_id"] != "sha256:old-snapshot"
    assert service_state["payload"] == {
        "revision_query": "改查去年并排除退款",
        "retrieval_calls": 0,
        "previous_snapshot_id": "sha256:old-snapshot",
        "approval_result": result["messages"][0].artifact["approval_result"],
    }


def test_query_intent_approval_accepts_all_structured_review_items_before_sql() -> None:
    """逐项审核全部接受后才推进 approved，修改项则重新检索。"""
    review_items = [
        {"id": "ambiguity:time", "question": "时间范围不明确", "status": "pending"},
        {"id": "ambiguity:refund", "question": "是否排除退款", "status": "pending"},
    ]
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    snapshot = build_query_label_snapshot(
        turn_id="turn-1",
        data_source_id="sales-pg",
        ability_version=1,
        retrieval=retrieval,
        intent="ranking",
        summary="查询华东销售额",
        ambiguities=["时间范围不明确", "是否排除退款"],
        labels=[
            {
                "label": "指标",
                "value": "销售额",
                "source": "database",
                "evidence_refs": [retrieval["evidences"][0]["ref"]],
            }
        ],
    )
    payload = {
        "retrieval": retrieval,
        "labels": snapshot,
        "approval": {"version": 1, "status": "awaiting_confirmation", "action": None, "source": None},
        "approval_request": {"source": "ask_intent_approval", "request_id": "data-query:review"},
        "review_items": review_items,
    }
    response = {
        "version": 1,
        "kind": "human_input_response",
        "source": "ask_intent_approval",
        "request_id": "data-query:review",
        "response_kind": "text",
        "value": json.dumps(
            {
                "kind": "data_query_review_response",
                "snapshot_id": snapshot["snapshot_id"],
                "final_action": "execute",
                "items": [
                    {"id": "ambiguity:time", "decision": "accept"},
                    {"id": "ambiguity:refund", "decision": "accept"},
                ],
            },
            ensure_ascii=False,
        ),
    }
    state = {
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": snapshot["snapshot_id"],
                "stage": "awaiting_confirmation",
                "data_source_id": "sales-pg",
                "payload": payload,
            }
        ],
        "messages": [HumanMessage(content="确认", additional_kwargs={"human_input_response": response})],
    }

    result = QueryIntentApprovalMiddleware(_config()).before_agent(state, MagicMock())

    assert result["service_states"][0]["stage"] == "approved"
    assert result["service_states"][0]["payload"]["approval"]["action"] == "execute"
    assert all(item["status"] == "accepted" for item in result["service_states"][0]["payload"]["review_items"])
    assert result["messages"][0].artifact["approval"]["status"] == "approved"
    assert result["messages"][0].artifact["approval_result"]["review"]["final_action"] == "execute"


def test_query_labels_service_mode_publishes_versioned_artifact_without_legacy_state() -> None:
    """DataAgent 标签只能写入 service_states，并携带服务端 snapshot_id。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    state = {
        "messages": [HumanMessage(id="turn-1", content="查询华东销售额最高的商品")],
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "stage": "retrieving",
                "data_source_id": "sales-pg",
                "payload": {"retrieval": retrieval},
            }
        ],
    }
    runtime = MagicMock()
    request = ToolCallRequest(
        tool_call={
            "name": "publish_query_labels",
            "id": "labels-call-1",
            "args": {
                "intent": "ranking",
                "summary": "查询华东销售额最高的商品",
                "ambiguities": [],
                "labels": [
                    {
                        "label": "指标",
                        "value": "销售额",
                        "source": "database",
                        "evidence_refs": [retrieval["evidences"][0]["ref"]],
                    }
                ],
            },
        },
        tool=None,
        state=state,
        runtime=runtime,
    )

    result = QueryLabelsMiddleware(require_retrieval=True, service_ability=_config()).wrap_tool_call(request, lambda _request: pytest.fail("placeholder tool must not run"))

    artifact = result.update["messages"][0].artifact
    assert artifact["kind"] == "data_query_labels"
    assert artifact["snapshot_id"].startswith("sha256:")
    assert "data_query_labels" not in result.update
    assert result.update["service_states"][0]["stage"] == "labels_published"
    assert artifact["approval_required"] is False
    assert artifact["approval_policy"]["required"] is False


def test_query_labels_missing_ambiguities_field_requires_confirmation() -> None:
    """模型省略 ambiguities 时必须写入明确的歧义占位，避免 UI 没有审核入口。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-missing-ambiguities",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    request = ToolCallRequest(
        tool_call={
            "name": "publish_query_labels",
            "id": "labels-missing-ambiguities",
            "args": {
                "intent": "aggregation",
                "summary": "查询销售额",
                "labels": [
                    {
                        "label": "指标",
                        "value": "销售额",
                        "source": "database",
                        "evidence_refs": [retrieval["evidences"][0]["ref"]],
                    }
                ],
            },
        },
        tool=None,
        state={
            "messages": [HumanMessage(id="turn-missing-ambiguities", content="查询销售额")],
            "service_states": [
                {
                    "service_name": "data_query",
                    "version": 1,
                    "turn_id": "turn-missing-ambiguities",
                    "stage": "retrieving",
                    "data_source_id": "sales-pg",
                    "payload": {"retrieval": retrieval},
                }
            ],
        },
        runtime=MagicMock(context={"thread_id": "thread-1"}),
    )

    result = QueryLabelsMiddleware(require_retrieval=True, service_ability=_config()).wrap_tool_call(
        request,
        lambda _request: pytest.fail("placeholder tool must not run"),
    )

    assert result.update["messages"][0].artifact["approval_required"] is True
    assert result.update["messages"][0].artifact["approval_policy"]["required"] is True
    assert result.update["messages"][0].artifact["ambiguity_items"][0]["question"]
    assert result.update["service_states"][0]["payload"]["labels"]["ambiguities_declared"] is False


def test_query_labels_middleware_keeps_one_label_snapshot_per_model_response() -> None:
    """同一 AIMessage 只保留首个标签发布，避免产生两个 pending human-input 请求。"""
    state = {
        "messages": [
            AIMessage(
                id="ai-label-parallel",
                content="",
                tool_calls=[
                    {"name": "publish_query_labels", "id": "labels-1", "args": {"intent": "ranking", "labels": []}},
                    {"name": "publish_query_labels", "id": "labels-2", "args": {"intent": "detail", "labels": []}},
                    {"name": "task", "id": "task-1", "args": {"subagent_type": "general-purpose"}},
                ],
            )
        ]
    }

    update = QueryLabelsMiddleware(require_retrieval=True, service_ability=_config()).after_model(state, MagicMock())

    assert [item["id"] for item in update["messages"][0].tool_calls] == ["labels-1", "task-1"]


def test_query_labels_non_interactive_run_still_publishes_snapshot() -> None:
    """非交互运行也要先稳定发布标签快照，审批策略由后续工具决定。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    state = {
        "messages": [HumanMessage(id="turn-1", content="查询销售额")],
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "stage": "retrieving",
                "data_source_id": "sales-pg",
                "payload": {"retrieval": retrieval},
            }
        ],
    }
    runtime = MagicMock()
    runtime.context = {"thread_id": "thread-1", "non_interactive": True}
    request = ToolCallRequest(
        tool_call={
            "name": "publish_query_labels",
            "id": "labels-call-non-interactive",
            "args": {
                "intent": "aggregation",
                "summary": "查询销售额",
                "ambiguities": ["未明确时间范围"],
                "labels": [
                    {
                        "label": "指标",
                        "value": "销售额",
                        "source": "database",
                        "evidence_refs": [retrieval["evidences"][0]["ref"]],
                    }
                ],
            },
        },
        tool=None,
        state=state,
        runtime=runtime,
    )

    result = QueryLabelsMiddleware(require_retrieval=True, service_ability=_config()).wrap_tool_call(
        request,
        lambda _request: pytest.fail("placeholder tool must not run"),
    )

    artifact = result.update["messages"][0].artifact
    service_state = result.update["service_states"][0]
    assert artifact["approval_required"] is True
    assert artifact["approval_policy"]["required"] is True
    assert "human_input" not in artifact
    assert service_state["stage"] == "labels_published"


def test_postgres_sql_validation_is_ast_based_and_bound_to_retrieval() -> None:
    """SQL 校验拒绝多语句、写操作和未检索对象，并生成稳定 digest。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    valid = validate_sql(
        "SELECT orders.region, SUM(orders.order_amount) FROM public.orders GROUP BY orders.region",
        config=_config(),
        retrieval=retrieval,
    )

    assert valid["valid"] is True
    assert valid["executable_sql"].endswith("LIMIT 500")
    assert valid["validation_digest"].startswith("sha256:")
    assert validate_sql("SELECT 1; DELETE FROM orders", config=_config(), retrieval=retrieval)["error_code"] == "SQL_MULTIPLE_STATEMENTS"
    assert validate_sql("DELETE FROM orders", config=_config(), retrieval=retrieval)["error_code"] == "SQL_READONLY_REQUIRED"
    assert (
        validate_sql(
            "WITH deleted AS (DELETE FROM public.orders RETURNING region) SELECT region FROM deleted",
            config=_config(),
            retrieval=retrieval,
        )["error_code"]
        == "SQL_READONLY_REQUIRED"
    )
    assert validate_sql("SELECT secret FROM customers", config=_config(), retrieval=retrieval)["valid"] is False
    assert validate_sql("SELECT * FROM public.orders", config=_config(), retrieval=retrieval)["error_code"] == "SQL_STAR_NOT_ALLOWED"
    assert validate_sql("SELECT COUNT(*) FROM public.orders", config=_config(), retrieval=retrieval)["valid"] is True
    assert validate_sql("SELECT pg_read_file('/etc/passwd') FROM public.orders", config=_config(), retrieval=retrieval)["error_code"] == "SQL_DANGEROUS_FUNCTION"
    assert validate_sql("SELECT 1", config=_config(), retrieval=retrieval)["error_code"] == "SQL_TABLE_REQUIRED"
    assert validate_sql("SELECT CURRENT_USER FROM public.orders", config=_config(), retrieval=retrieval)["error_code"] == "SQL_DANGEROUS_FUNCTION"
    assert validate_sql("SELECT o.region FROM public.orders AS o", config=_config(), retrieval=retrieval)["valid"] is True
    assert validate_sql("SELECT region FROM public.orders", config=_config(), retrieval=retrieval)["valid"] is True
    assert (
        validate_sql(
            "SELECT orders.region, SUM(orders.order_amount) AS total FROM public.orders GROUP BY orders.region ORDER BY total DESC",
            config=_config(),
            retrieval=retrieval,
        )["valid"]
        is True
    )
    assert (
        validate_sql(
            "WITH regional AS (SELECT orders.region FROM public.orders) SELECT regional.region FROM regional",
            config=_config(),
            retrieval=retrieval,
        )["valid"]
        is True
    )


def test_mysql_sql_validation_is_ast_based_and_bound_to_logical_source() -> None:
    """MySQL 校验必须接受业务查询并拒绝写操作、系统库和危险函数。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-mysql",
        data_source_id="sales-mysql",
        binding=_mysql_binding(),
    )
    config = _mysql_config()

    valid = validate_sql(
        "SELECT orders.region, SUM(orders.order_amount) FROM text2sql.orders GROUP BY orders.region",
        config=config,
        retrieval=retrieval,
        snapshot_id="snapshot-mysql",
    )

    assert valid["valid"] is True
    assert valid["database_type"] == "mysql"
    assert valid["executable_sql"].endswith("LIMIT 500")
    assert validate_sql("SELECT 1; DELETE FROM orders", config=config, retrieval=retrieval)["error_code"] == "SQL_MULTIPLE_STATEMENTS"
    assert validate_sql("DELETE FROM orders", config=config, retrieval=retrieval)["error_code"] == "SQL_READONLY_REQUIRED"
    assert validate_sql("SELECT SLEEP(1), orders.region FROM orders", config=config, retrieval=retrieval)["error_code"] == "SQL_DANGEROUS_FUNCTION"
    assert validate_sql("SELECT GET_LOCK('data-agent', 1), orders.region FROM orders", config=config, retrieval=retrieval)["error_code"] == "SQL_DANGEROUS_FUNCTION"
    assert validate_sql("SELECT LOAD_FILE('/etc/passwd'), orders.region FROM orders", config=config, retrieval=retrieval)["error_code"] == "SQL_DANGEROUS_FUNCTION"
    assert validate_sql("SELECT @captured := orders.region FROM orders", config=config, retrieval=retrieval)["error_code"] == "SQL_READONLY_REQUIRED"
    assert validate_sql("SELECT table_name FROM information_schema.tables", config=config, retrieval=retrieval)["error_code"] == "SQL_SYSTEM_DATABASE_FORBIDDEN"
    assert validate_sql("/*!50000 SELECT orders.region FROM orders */", config=config, retrieval=retrieval)["error_code"] == "SQL_EXECUTABLE_COMMENT_FORBIDDEN"


def test_sql_validation_digest_binds_server_data_source_fingerprint() -> None:
    """相同 SQL 在服务端数据源绑定变化后不得复用旧 validation digest。"""
    config = _mysql_config()
    first_retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-mysql",
        data_source_id="sales-mysql",
        binding=_mysql_binding("sha256:binding-one"),
    )
    second_retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-mysql",
        data_source_id="sales-mysql",
        binding=_mysql_binding("sha256:binding-two"),
    )
    sql = "SELECT orders.region FROM orders"

    first = validate_sql(sql, config=config, retrieval=first_retrieval, snapshot_id="snapshot-mysql")
    second = validate_sql(sql, config=config, retrieval=second_retrieval, snapshot_id="snapshot-mysql")

    assert first["valid"] is True
    assert second["valid"] is True
    assert first["validation_digest"] != second["validation_digest"]


def test_p0_sql_validation_failures_never_reach_database_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """危险 SQL、系统信息和无业务表查询在建立连接前必须被拒绝。"""
    driver_calls = 0

    def fail_if_called(*_args: Any, **_kwargs: Any) -> tuple[list[str], list[dict[str, Any]]]:
        nonlocal driver_calls
        driver_calls += 1
        raise AssertionError("被拒绝的 SQL 不得进入数据库驱动")

    monkeypatch.setattr("deerflow.agents.service_agent.sql_executor._execute_postgres", fail_if_called)
    config = _config()
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-p0",
        data_source_id="sales-pg",
        binding=_binding(),
    )

    for sql in (
        "SELECT 1",
        "SELECT CURRENT_USER FROM orders",
        "WITH changed AS (DELETE FROM orders RETURNING id) SELECT id FROM changed",
        "SELECT orders.region FROM orders; DELETE FROM orders",
    ):
        validation = validate_sql(sql, config=config, retrieval=retrieval, snapshot_id="sha256:p0")
        assert validation["valid"] is False
        execution = execute_sql(
            sql,
            validation_digest=str(validation.get("validation_digest") or ""),
            validation=validation,
            config=config,
        )
        assert execution["ok"] is False
        assert execution["error_code"] == "SQL_DIGEST_MISMATCH"

    assert driver_calls == 0


def test_sql_tools_are_factory_scoped_and_keep_unique_names() -> None:
    """SQL 工具只由 sql-subagent 工厂提供，不进入默认 BUILTIN_TOOLS。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    state = {
        "snapshot_id": "sha256:snapshot",
        "payload": {"retrieval": retrieval, "approval": {"status": "approved", "action": "execute"}},
    }

    tools = build_sql_tools(_config(), state)
    assert [tool.name for tool in tools] == ["data_validate_sql", "data_execute_sql"]
    assert tools[1].args_schema.model_json_schema()["required"] == ["sql", "validation_digest"]


def test_sql_only_snapshot_never_exposes_database_execution_tool() -> None:
    """sql_only 授权只能生成和校验 SQL，子代理工具面不得包含执行入口。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    state = {
        "snapshot_id": "sha256:snapshot",
        "payload": {"retrieval": retrieval, "approval": {"status": "approved", "action": "sql_only"}},
    }

    tools = build_sql_tools(_config(), state)

    assert [tool.name for tool in tools] == ["data_validate_sql"]


def test_sql_tools_attach_artifact_for_agent_tool_messages() -> None:
    """SQL 工具在真实 Agent 工具节点中必须同时输出 content 和 artifact。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    tools = build_sql_tools(
        _config(),
        {
            "snapshot_id": "sha256:snapshot",
            "payload": {"retrieval": retrieval, "approval": {"status": "approved", "action": "sql_only"}},
        },
    )
    model = _DataQueryFlowModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "data_validate_sql",
                        "id": "validate-artifact-1",
                        "args": {"sql": "SELECT orders.region FROM public.orders"},
                    }
                ],
            ),
            AIMessage(content="已生成 SQL。"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=tools,
        state_schema=ThreadState,
    )

    final_state = graph.invoke({"messages": [HumanMessage(id="turn-artifact-1", content="生成 SQL")]})

    message = next(message for message in final_state["messages"] if isinstance(message, ToolMessage) and message.name == "data_validate_sql")
    assert json.loads(message.content)["valid"] is True
    assert message.artifact["valid"] is True
    assert message.artifact["snapshot_id"] == "sha256:snapshot"


def test_sql_execution_tool_allows_only_one_authorized_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一快照的授权 SQL 即使连接失败也不能自动重复执行。"""
    monkeypatch.delenv("DATA_AGENT_SQL_DSN", raising=False)
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    tools = build_sql_tools(
        _config(),
        {
            "snapshot_id": "sha256:snapshot",
            "payload": {"retrieval": retrieval, "approval": {"status": "approved", "action": "execute"}},
        },
    )
    validation = json.loads(tools[0].invoke({"sql": "SELECT orders.region FROM public.orders"}))

    first = json.loads(tools[1].invoke({"sql": validation["executable_sql"], "validation_digest": validation["validation_digest"]}))
    second = json.loads(tools[1].invoke({"sql": validation["executable_sql"], "validation_digest": validation["validation_digest"]}))

    assert first["error_code"] == "SQL_DSN_MISSING"
    assert first["validation_digest"] == validation["validation_digest"]
    assert second["error_code"] == "SQL_EXECUTION_ALREADY_ATTEMPTED"


def test_execute_sql_uses_request_secret_without_exposing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """SQL 执行可以使用请求级 secret:// DSN，返回结构中不得出现 Secret 内容。"""
    raw = _mysql_config().model_dump()
    raw["sql_execution"]["dsn_env"] = "secret://data-agent-mysql"
    config = DataQueryServiceAbilityConfig.model_validate(raw)
    secret_dsn = "mysql+pymysql://readonly:super-secret@db.local:3308/text2sql"
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@index.local:55433/text2sql")
    binding = resolve_data_source_binding(config, secrets={"data-agent-mysql": secret_dsn})
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-secret",
        data_source_id=config.data_source_id,
        binding=binding,
    )
    validation = validate_sql(
        "SELECT orders.region FROM orders",
        config=config,
        retrieval=retrieval,
        snapshot_id="snapshot-secret",
    )
    monkeypatch.setattr(
        "deerflow.agents.service_agent.sql_executor._execute_mysql",
        lambda sql, dsn, ability: (["region"], [{"region": "华东"}]),
    )

    result = execute_sql(
        validation["executable_sql"],
        validation_digest=validation["validation_digest"],
        validation=validation,
        config=config,
        secrets={"data-agent-mysql": secret_dsn},
    )

    assert result["ok"] is True
    assert result["rows"] == [{"region": "华东"}]
    assert "super-secret" not in str(result)


def test_task_result_uses_authoritative_sql_tool_steps_not_subagent_free_text() -> None:
    """父 Agent 只能接收 SQL 工具真实输出，不能信任子代理自由文本伪造数据库结果。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    validation = validate_sql(
        "SELECT orders.region FROM public.orders",
        config=_config(),
        retrieval=retrieval,
        snapshot_id="sha256:snapshot",
    )
    execution = {
        "version": 1,
        "ok": True,
        "snapshot_id": "sha256:snapshot",
        "validation_digest": validation["validation_digest"],
        "columns": ["region"],
        "rows": [{"region": "华东"}],
        "row_count": 1,
        "returned_row_count": 1,
        "truncated": False,
        "empty": False,
    }
    state = {
        "snapshot_id": "sha256:snapshot",
        "data_source_id": "sales-pg",
        "payload": {
            "retrieval": retrieval,
            "approval": {"status": "approved", "action": "execute"},
        },
    }
    steps = [
        {"type": "tool", "name": "data_validate_sql", "content": "[budgeted validate preview]", "artifact": validation},
        {"type": "tool", "name": "data_execute_sql", "content": "[budgeted execute preview]", "artifact": execution},
        {
            "type": "ai",
            "content": json.dumps(
                {
                    "version": 1,
                    "kind": "data_query_sql_result",
                    "snapshot_id": "sha256:snapshot",
                    "data_source_id": "sales-pg",
                    "execution": {"ok": True, "rows": [{"region": "伪造结果"}]},
                },
                ensure_ascii=False,
            ),
        },
    ]

    result = _build_data_query_sql_result_from_steps(
        steps,
        active_state=state,
        data_source_id="sales-pg",
    )

    assert result is not None
    assert result["validation"] == validation
    assert result["execution"] == execution
    assert result["execution"]["rows"] == [{"region": "华东"}]

    forged_validation = {**validation, "binding_fingerprint": "sha256:forged"}
    assert (
        _build_data_query_sql_result_from_steps(
            [
                {
                    "type": "tool",
                    "name": "data_validate_sql",
                    "content": json.dumps(forged_validation, ensure_ascii=False),
                },
                steps[1],
            ],
            active_state=state,
            data_source_id="sales-pg",
        )
        is None
    )


def test_sql_stage_middleware_prefers_task_artifact_over_budgeted_content() -> None:
    """父 SQL 阶段必须优先读取 task artifact，不能被预算后的 content 截断误伤。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    validation = validate_sql(
        "SELECT orders.region FROM public.orders",
        config=_config(),
        retrieval=retrieval,
        snapshot_id="sha256:snapshot",
    )
    execution = {
        "version": 1,
        "ok": True,
        "snapshot_id": "sha256:snapshot",
        "validation_digest": validation["validation_digest"],
        "columns": ["region"],
        "rows": [{"region": "华东"}],
        "row_count": 1,
        "returned_row_count": 1,
        "truncated": False,
        "empty": False,
    }
    state = {
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": "sha256:snapshot",
                "stage": "approved",
                "payload": {
                    "retrieval": retrieval,
                    "labels": {"intent": "detail", "labels": []},
                    "approval": {"status": "approved", "action": "execute"},
                },
            }
        ]
    }
    request = ToolCallRequest(
        tool_call={
            "name": "task",
            "id": "task-artifact",
            "args": {"description": "SQL", "prompt": "ignored", "subagent_type": "sql-subagent"},
        },
        tool=None,
        state=state,
        runtime=MagicMock(),
    )

    def handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="Task Succeeded. Result: [budgeted content preview]",
            artifact={
                "version": 1,
                "kind": "data_query_sql_result",
                "snapshot_id": "sha256:snapshot",
                "data_source_id": "sales-pg",
                "validation": validation,
                "execution": execution,
            },
            tool_call_id="task-artifact",
            name="task",
        )

    result = SqlStageMiddleware(_config()).wrap_tool_call(request, handler)

    assert result.update["service_states"][0]["stage"] == "succeeded"
    assert result.update["messages"][0].artifact["execution"] == execution


def test_sql_stage_middleware_surfaces_task_failure_code_instead_of_contract_error() -> None:
    """task 已经给出 SQL_* 失败码时，父阶段应透传真实失败原因。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    state = {
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": "sha256:snapshot",
                "stage": "approved",
                "payload": {
                    "retrieval": retrieval,
                    "labels": {"intent": "detail", "labels": []},
                    "approval": {"status": "approved", "action": "execute"},
                },
            }
        ]
    }
    request = ToolCallRequest(
        tool_call={
            "name": "task",
            "id": "task-failed",
            "args": {"description": "SQL", "prompt": "ignored", "subagent_type": "sql-subagent"},
        },
        tool=None,
        state=state,
        runtime=MagicMock(),
    )

    def handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="Task failed. Error: SQL_SUBAGENT_TOOL_RESULT_INVALID",
            tool_call_id="task-failed",
            name="task",
            additional_kwargs=make_subagent_additional_kwargs(
                "failed",
                error="SQL_SUBAGENT_TOOL_RESULT_INVALID",
            ),
        )

    result = SqlStageMiddleware(_config()).wrap_tool_call(request, handler)

    assert json.loads(result.update["messages"][0].content)["error_code"] == "SQL_SUBAGENT_TOOL_RESULT_INVALID"
    assert read_subagent_result_metadata(result.update["messages"][0].additional_kwargs) == {
        "status": "failed",
        "error": "SQL_SUBAGENT_TOOL_RESULT_INVALID",
    }


def test_sql_only_authoritative_result_rejects_any_execution_step() -> None:
    """sql_only 子任务出现执行工具结果时必须整体拒绝，不能把结果投影给父状态。"""
    state = {
        "snapshot_id": "sha256:snapshot",
        "data_source_id": "sales-pg",
        "payload": {"approval": {"status": "approved", "action": "sql_only"}},
    }
    steps = [
        {
            "type": "tool",
            "name": "data_validate_sql",
            "content": json.dumps(
                {
                    "version": 1,
                    "valid": True,
                    "snapshot_id": "sha256:snapshot",
                    "executable_sql": "SELECT region FROM orders LIMIT 1",
                    "sql_sha256": "sha256:sql",
                    "validation_digest": "sha256:validation",
                }
            ),
        },
        {
            "type": "tool",
            "name": "data_execute_sql",
            "content": json.dumps({"version": 1, "ok": True, "snapshot_id": "sha256:snapshot"}),
        },
    ]

    assert _build_data_query_sql_result_from_steps(steps, active_state=state, data_source_id="sales-pg") is None


def test_sql_stage_middleware_replaces_free_text_prompt_with_json_envelope() -> None:
    """父 Agent 委派 SQL 时必须传严格 JSON envelope。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    state = {
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": "sha256:snapshot",
                "stage": "approved",
                "payload": {
                    "retrieval": retrieval,
                    "labels": {"intent": "ranking", "labels": []},
                    "approval": {"status": "approved", "action": "sql_only"},
                },
            }
        ]
    }
    runtime = MagicMock()
    runtime.context = {
        "thread_id": "thread-1",
        "run_id": "run-parent-1",
        "data_query_service_ability": _config().model_dump(mode="json"),
        "data_query_sql_subagent_allowed": True,
    }
    request = ToolCallRequest(
        tool_call={
            "name": "task",
            "id": "task-1",
            "args": {"description": "SQL", "prompt": "自由文本", "subagent_type": "sql-subagent"},
        },
        tool=None,
        state=state,
        runtime=runtime,
    )

    def handler(next_request: ToolCallRequest) -> ToolMessage:
        envelope = json.loads(next_request.tool_call["args"]["prompt"])
        assert envelope["kind"] == "data_query_sql_request"
        assert envelope["snapshot_id"] == "sha256:snapshot"
        assert envelope["thread_id"] == "thread-1"
        assert envelope["parent_run_id"] == "run-parent-1"
        assert envelope["database_type"] == "postgresql"
        assert envelope["max_execution_attempts"] == 3
        validation = validate_sql(
            "SELECT orders.region FROM public.orders LIMIT 500",
            config=_config(),
            retrieval=retrieval,
            snapshot_id="sha256:snapshot",
        )
        return ToolMessage(
            content=(f'Task Succeeded. Result: {{"version":1,"kind":"data_query_sql_result","snapshot_id":"sha256:snapshot","data_source_id":"sales-pg","validation":{json.dumps(validation, ensure_ascii=False)},"execution":null}}'),
            tool_call_id="task-1",
            name="task",
        )

    result = SqlStageMiddleware(_config()).wrap_tool_call(request, handler)

    assert result.update["service_states"][0]["stage"] == "succeeded"
    assert result.update["messages"][0].artifact["kind"] == "data_query_sql_result"


def test_sql_stage_middleware_blocks_target_when_custom_agent_did_not_allowlist_it() -> None:
    """客户端即使手动开启 task，也不能绕过 custom-agent 的 SQL SubAgent allowlist。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    state = {
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": "sha256:snapshot",
                "stage": "approved",
                "payload": {
                    "retrieval": retrieval,
                    "labels": {"intent": "detail", "labels": []},
                    "approval": {"status": "approved", "action": "sql_only"},
                },
            }
        ]
    }
    runtime = MagicMock()
    runtime.context = {
        "data_query_service_ability": _config().model_dump(mode="json"),
        "data_query_sql_subagent_allowed": False,
    }
    request = ToolCallRequest(
        tool_call={
            "name": "task",
            "id": "task-not-allowed",
            "args": {"description": "SQL", "prompt": "ignored", "subagent_type": "sql-subagent"},
        },
        tool=None,
        state=state,
        runtime=runtime,
    )

    result = SqlStageMiddleware(_config()).wrap_tool_call(
        request,
        lambda _request: pytest.fail("未授权 SQL SubAgent 不得进入 task handler"),
    )

    assert not isinstance(result, ToolMessage)
    message = result.update["messages"][0]
    assert json.loads(message.content)["error_code"] == "SQL_STAGE_NOT_APPROVED"
    assert read_subagent_result_metadata(message.additional_kwargs) == {
        "status": "failed",
        "error": "SQL_STAGE_NOT_APPROVED",
    }


def test_sql_stage_middleware_keeps_only_one_sql_subagent_call_per_model_response() -> None:
    """同一 AIMessage 不能并行启动两个 SQL SubAgent，避免同快照重复执行。"""
    state = {
        "messages": [
            AIMessage(
                id="ai-sql-parallel",
                content="",
                tool_calls=[
                    {"name": "task", "id": "sql-1", "args": {"subagent_type": "sql-subagent"}},
                    {"name": "task", "id": "sql-2", "args": {"subagent_type": "sql-subagent"}},
                    {"name": "task", "id": "general-1", "args": {"subagent_type": "general-purpose"}},
                ],
            )
        ]
    }

    update = SqlStageMiddleware(_config()).after_model(state, MagicMock())

    tool_calls = update["messages"][0].tool_calls
    assert [item["id"] for item in tool_calls] == ["sql-1", "general-1"]


def test_sql_only_parent_rejects_fabricated_execution_payload() -> None:
    """sql_only 返回中只要出现 execution，父 middleware 就必须拒绝整个子代理结果。"""
    retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="turn-1",
        data_source_id="sales-pg",
        binding=_binding(),
    )
    state = {
        "service_states": [
            {
                "service_name": "data_query",
                "version": 1,
                "turn_id": "turn-1",
                "snapshot_id": "sha256:snapshot",
                "stage": "approved",
                "payload": {
                    "retrieval": retrieval,
                    "labels": {"intent": "detail", "labels": []},
                    "approval": {"status": "approved", "action": "sql_only"},
                },
            }
        ]
    }
    request = ToolCallRequest(
        tool_call={
            "name": "task",
            "id": "task-sql-only",
            "args": {"description": "SQL", "prompt": "ignored", "subagent_type": "sql-subagent"},
        },
        tool=None,
        state=state,
        runtime=MagicMock(),
    )
    validation = validate_sql(
        "SELECT orders.region FROM public.orders",
        config=_config(),
        retrieval=retrieval,
        snapshot_id="sha256:snapshot",
    )

    def handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=(
                "Task Succeeded. Result: "
                + json.dumps(
                    {
                        "version": 1,
                        "kind": "data_query_sql_result",
                        "snapshot_id": "sha256:snapshot",
                        "data_source_id": "sales-pg",
                        "validation": validation,
                        "execution": {
                            "version": 1,
                            "ok": True,
                            "snapshot_id": "sha256:snapshot",
                            "validation_digest": validation["validation_digest"],
                            "columns": ["region"],
                            "rows": [{"region": "伪造"}],
                            "row_count": 1,
                            "returned_row_count": 1,
                            "truncated": False,
                            "empty": False,
                        },
                    },
                    ensure_ascii=False,
                )
            ),
            tool_call_id="task-sql-only",
            name="task",
        )

    result = SqlStageMiddleware(_config()).wrap_tool_call(request, handler)

    assert not isinstance(result, ToolMessage)
    message = result.update["messages"][0]
    assert json.loads(message.content)["error_code"] == "SQL_SUBAGENT_CONTRACT_INVALID"
    assert read_subagent_result_metadata(message.additional_kwargs) == {
        "status": "failed",
        "error": "SQL_SUBAGENT_CONTRACT_INVALID",
    }


def test_fake_agent_completes_table_rag_labels_sql_and_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 create_agent 工具循环应复用同一图完成 DataAgent 自动确认闭环。"""
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", "postgresql://readonly:secret@db.local:5432/sales")
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@db.local:5432/sales")
    config = _config("auto")
    binding = resolve_data_source_binding(config)
    expected_retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="expected-turn",
        data_source_id="sales-pg",
        binding=binding,
    )
    evidence_ref = expected_retrieval["evidences"][0]["ref"]

    @tool("sqlrag_retrieve")
    def fake_tablerag(operation: str = "hybrid-search", query: str | None = None, queries: list[str] | None = None, table_names: list[str] | None = None) -> str:
        """返回固定 TableRAG 检索结果。"""
        return json.dumps(_retrieval_payload(), ensure_ascii=False)

    @tool("task")
    def fake_task(description: str, prompt: str, subagent_type: str) -> str:
        """按父级 JSON envelope 返回固定 SQL 子代理结果。"""
        envelope = json.loads(prompt)
        validation = validate_sql(
            "SELECT orders.region FROM public.orders LIMIT 500",
            config=config,
            retrieval=expected_retrieval,
            snapshot_id=envelope["snapshot_id"],
        )
        execution = {
            "version": 1,
            "ok": True,
            "snapshot_id": envelope["snapshot_id"],
            "validation_digest": validation["validation_digest"],
            "columns": ["region"],
            "rows": [],
            "row_count": 0,
            "returned_row_count": 0,
            "truncated": False,
            "empty": True,
        }
        result = {
            "version": 1,
            "kind": "data_query_sql_result",
            "snapshot_id": envelope["snapshot_id"],
            "data_source_id": envelope["data_source_id"],
            "validation": validation,
            "execution": execution,
        }
        return f"Task Succeeded. Result: {json.dumps(result, ensure_ascii=False)}"

    model = _DataQueryFlowModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "sqlrag_retrieve", "id": "rag-1", "args": {"operation": "hybrid-search", "query": "查询华东销售额"}}]),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "publish_query_labels",
                        "id": "labels-1",
                        "args": {
                            "intent": "ranking",
                            "summary": "查询华东销售额",
                            "ambiguities": [],
                            "labels": [
                                {
                                    "label": "指标",
                                    "value": "销售额",
                                    "source": "database",
                                    "evidence_refs": [evidence_ref],
                                }
                            ],
                        },
                    }
                ],
            ),
            AIMessage(content="", tool_calls=[{"name": "task", "id": "task-1", "args": {"description": "SQL", "prompt": "ignored", "subagent_type": "sql-subagent"}}]),
            AIMessage(content="查询成功，结果为空。"),
        ]
    )
    ability = DataAgentServiceAbility(config)
    graph = create_agent(
        model=model,
        tools=[fake_tablerag, publish_query_labels_tool, fake_task],
        middleware=ability.build_middlewares(),
        state_schema=ThreadState,
    )

    final_state = graph.invoke({"messages": [HumanMessage(id="turn-1", content="查询华东销售额")]})

    active = final_state["service_states"][0]
    artifacts = [message.artifact for message in final_state["messages"] if isinstance(message, ToolMessage) and message.artifact]
    assert active["stage"] == "succeeded"
    assert [artifact["kind"] for artifact in artifacts] == ["data_query_labels", "data_query_sql_result"]
    assert final_state["messages"][-1].content == "查询成功，结果为空。"


def test_fake_agent_publishes_labels_and_waits_for_intent_approval_when_ambiguity_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """存在结构化 ambiguity 时图必须停在标签快照，等待模型调用意图审批工具。"""
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", "postgresql://readonly:secret@db.local:5432/sales")
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@db.local:5432/sales")
    config = _config("on_ambiguity")
    binding = resolve_data_source_binding(config)
    expected_retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="expected-turn",
        data_source_id="sales-pg",
        binding=binding,
    )
    evidence_ref = expected_retrieval["evidences"][0]["ref"]

    @tool("sqlrag_retrieve")
    def fake_tablerag(operation: str = "hybrid-search", query: str | None = None, queries: list[str] | None = None, table_names: list[str] | None = None) -> str:
        """返回固定 TableRAG 检索结果。"""
        return json.dumps(_retrieval_payload(), ensure_ascii=False)

    model = _DataQueryFlowModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "sqlrag_retrieve", "id": "rag-review-1", "args": {"operation": "hybrid-search", "query": "查询华东销售额"}}]),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "publish_query_labels",
                        "id": "labels-review-1",
                        "args": {
                            "intent": "ranking",
                            "summary": "查询华东销售额",
                            "ambiguities": ["是否排除退款"],
                            "labels": [
                                {
                                    "label": "指标",
                                    "value": "销售额",
                                    "source": "database",
                                    "evidence_refs": [evidence_ref],
                                }
                            ],
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "ask_intent_approval", "id": "approval-review-1", "args": {}}],
            ),
        ]
    )
    ability = DataAgentServiceAbility(config)
    graph = create_agent(
        model=model,
        tools=[fake_tablerag, publish_query_labels_tool, ask_intent_approval_tool],
        middleware=ability.build_middlewares(),
        state_schema=ThreadState,
    )

    final_state = graph.invoke({"messages": [HumanMessage(id="turn-review-1", content="查询华东销售额")]})

    active = final_state["service_states"][0]
    label_message = next(message for message in final_state["messages"] if isinstance(message, ToolMessage) and message.name == "publish_query_labels")
    approval_message = next(message for message in final_state["messages"] if isinstance(message, ToolMessage) and message.name == "ask_intent_approval")
    assert active["stage"] == "awaiting_confirmation"
    assert label_message.artifact["approval_required"] is True
    assert label_message.artifact["approval_policy"]["required"] is True
    assert "human_input" not in label_message.artifact
    assert label_message.artifact["ambiguity_items"][0]["question"] == "是否排除退款"
    assert approval_message.artifact["human_input"]["source"] == "ask_intent_approval"
    assert approval_message.artifact["approval"]["status"] == "awaiting_confirmation"
    assert not any(getattr(message, "type", None) == "ai" and getattr(message, "content", "") == "查询成功，结果为空。" for message in final_state["messages"])


# ADD: 验证 human-input v1 隐藏回复可以从 checkpoint 恢复，并在逐项确认后继续 SQL 闭环。
def test_fake_agent_resumes_from_review_checkpoint_after_human_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """人工逐项确认后，图应恢复 approved 状态并继续 SQL SubAgent。"""
    monkeypatch.setenv("DATA_AGENT_SQL_DSN", "postgresql://readonly:secret@db.local:5432/sales")
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://indexer:secret@db.local:5432/sales")
    config = _config("on_ambiguity")
    binding = resolve_data_source_binding(config)
    expected_retrieval = build_retrieval_context(
        _retrieval_payload(),
        tool_name="sqlrag_retrieve",
        turn_id="expected-turn",
        data_source_id="sales-pg",
        binding=binding,
    )
    evidence_ref = expected_retrieval["evidences"][0]["ref"]

    @tool("sqlrag_retrieve")
    def fake_tablerag(operation: str = "hybrid-search", query: str | None = None, queries: list[str] | None = None, table_names: list[str] | None = None) -> str:
        """返回固定 TableRAG 检索结果。"""
        return json.dumps(_retrieval_payload(), ensure_ascii=False)

    @tool("task")
    def fake_task(description: str, prompt: str, subagent_type: str) -> str:
        """返回固定 SQL 子代理结构化结果。"""
        envelope = json.loads(prompt)
        validation = validate_sql(
            "SELECT orders.region FROM public.orders LIMIT 500",
            config=config,
            retrieval=expected_retrieval,
            snapshot_id=envelope["snapshot_id"],
        )
        result = {
            "version": 1,
            "kind": "data_query_sql_result",
            "snapshot_id": envelope["snapshot_id"],
            "data_source_id": envelope["data_source_id"],
            "validation": validation,
            "execution": {
                "version": 1,
                "ok": True,
                "snapshot_id": envelope["snapshot_id"],
                "validation_digest": validation["validation_digest"],
                "columns": ["region"],
                "rows": [],
                "row_count": 0,
                "returned_row_count": 0,
                "truncated": False,
                "empty": True,
            },
        }
        return f"Task Succeeded. Result: {json.dumps(result, ensure_ascii=False)}"

    model = _DataQueryFlowModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "sqlrag_retrieve", "id": "rag-resume-1", "args": {"operation": "hybrid-search", "query": "查询华东销售额"}}]),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "publish_query_labels",
                        "id": "labels-resume-1",
                        "args": {
                            "intent": "ranking",
                            "summary": "查询华东销售额",
                            "ambiguities": ["是否排除退款"],
                            "labels": [
                                {
                                    "label": "指标",
                                    "value": "销售额",
                                    "source": "database",
                                    "evidence_refs": [evidence_ref],
                                }
                            ],
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_intent_approval",
                        "id": "approval-resume-1",
                        "args": {},
                    }
                ],
            ),
            AIMessage(content="", tool_calls=[{"name": "task", "id": "task-resume-1", "args": {"description": "SQL", "prompt": "ignored", "subagent_type": "sql-subagent"}}]),
            AIMessage(content="查询成功，结果为空。"),
        ]
    )
    ability = DataAgentServiceAbility(config)
    graph = create_agent(
        model=model,
        tools=[fake_tablerag, publish_query_labels_tool, ask_intent_approval_tool, fake_task],
        middleware=ability.build_middlewares(),
        state_schema=ThreadState,
        checkpointer=InMemorySaver(),
    )
    run_config = {"configurable": {"thread_id": "data-query-review-resume"}}

    paused_state = graph.invoke({"messages": [HumanMessage(id="turn-resume-1", content="查询华东销售额")]}, config=run_config)
    paused_service = paused_state["service_states"][0]
    review_items = paused_service["payload"]["review_items"]
    response = {
        "version": 1,
        "kind": "human_input_response",
        "source": "ask_intent_approval",
        "request_id": paused_service["payload"]["approval_request"]["request_id"],
        "response_kind": "text",
        "value": json.dumps(
            {
                "kind": "data_query_review_response",
                "snapshot_id": paused_service["snapshot_id"],
                "final_action": "execute",
                "items": [{"id": item["id"], "decision": "accept"} for item in review_items],
            },
            ensure_ascii=False,
        ),
    }

    resumed_state = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    id="hidden-review-resume-1",
                    content="已确认",
                    additional_kwargs={"hide_from_ui": True, "human_input_response": response},
                )
            ]
        },
        config=run_config,
    )

    active = resumed_state["service_states"][0]
    artifacts = [message.artifact for message in resumed_state["messages"] if isinstance(message, ToolMessage) and message.artifact]
    assert paused_service["stage"] == "awaiting_confirmation"
    assert active["stage"] == "succeeded"
    assert [artifact["kind"] for artifact in artifacts] == ["data_query_labels", "data_query_labels", "data_query_sql_result"]
    assert resumed_state["messages"][-1].content == "查询成功，结果为空。"
