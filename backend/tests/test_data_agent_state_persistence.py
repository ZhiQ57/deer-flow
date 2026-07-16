"""DataAgent service_states checkpoint、历史和 API 序列化测试。"""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deerflow.agents.thread_state import ThreadState
from deerflow.runtime.serialization import serialize_channel_values_for_api


def _service_state() -> dict:
    """构造可持久化且不包含 Secret 的 DataAgent 状态。"""
    return {
        "service_name": "data_query",
        "version": 1,
        "turn_id": "turn-checkpoint",
        "snapshot_id": "sha256:checkpoint-snapshot",
        "stage": "awaiting_confirmation",
        "data_source_id": "text2sql-mysql-local",
        "payload": {
            "approval": {"status": "awaiting_confirmation", "action": None},
            "retrieval": {
                "ok": True,
                "retrieval_digest": "retrieval:sha256:test",
                "binding": {"binding_fingerprint": "sha256:binding"},
            },
        },
        "updated_at": "2026-07-16T00:00:00Z",
    }


def test_service_states_checkpoint_round_trip_and_resume() -> None:
    """LangGraph checkpoint 必须完整恢复活动 DataAgent 快照。"""
    checkpointer = InMemorySaver()
    builder = StateGraph(ThreadState)

    def persist(_state: ThreadState) -> dict:
        """写入唯一 DataAgent 服务状态。"""
        return {"service_states": [_service_state()]}

    builder.add_node("persist", persist)
    builder.add_edge(START, "persist")
    builder.add_edge("persist", END)
    graph = builder.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "data-agent-checkpoint"}}

    graph.invoke(
        {"messages": [HumanMessage(id="turn-checkpoint", content="查询女性因素诊断")], "service_states": []},
        config=config,
    )
    restored = graph.get_state(config).values

    assert restored["service_states"] == [_service_state()]
    assert restored["messages"][0].id == "turn-checkpoint"


def test_service_states_survive_api_serialization_without_internal_filtering() -> None:
    """实时 values 与历史 API 序列化不得过滤、截断或改写 service_states。"""
    raw = {
        "messages": [HumanMessage(id="turn-checkpoint", content="查询女性因素诊断")],
        "service_states": [_service_state()],
        "__pregel_tasks": ["internal"],
    }

    serialized = serialize_channel_values_for_api(raw)

    assert "__pregel_tasks" not in serialized
    assert serialized["service_states"] == [_service_state()]
    assert serialized["messages"][0]["id"] == "turn-checkpoint"
