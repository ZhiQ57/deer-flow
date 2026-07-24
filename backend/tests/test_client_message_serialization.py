"""Tests for DeerFlowClient message serialization helpers."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.client import DeerFlowClient


def test_serialize_ai_message_preserves_additional_kwargs():
    message = AIMessage(
        content="done",
        additional_kwargs={
            "token_usage_attribution": {
                "version": 1,
                "kind": "final_answer",
                "shared_attribution": False,
                "actions": [],
            }
        },
        usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    )

    serialized = DeerFlowClient._serialize_message(message)

    assert serialized["type"] == "ai"
    assert serialized["usage_metadata"] == {
        "input_tokens": 12,
        "output_tokens": 3,
        "total_tokens": 15,
    }
    assert serialized["additional_kwargs"] == {
        "token_usage_attribution": {
            "version": 1,
            "kind": "final_answer",
            "shared_attribution": False,
            "actions": [],
        }
    }


def test_serialize_human_message_preserves_additional_kwargs():
    message = HumanMessage(
        content="hello",
        additional_kwargs={"files": [{"name": "diagram.png"}]},
    )

    serialized = DeerFlowClient._serialize_message(message)

    assert serialized == {
        "type": "human",
        "content": "hello",
        "id": None,
        "additional_kwargs": {"files": [{"name": "diagram.png"}]},
    }


def test_serialize_tool_message_preserves_data_query_artifact():
    """嵌入式 Client 的 ToolMessage 实时/values 序列化必须保留 DataAgent artifact。"""
    artifact = {
        "version": 1,
        "kind": "data_query_labels",
        "service_name": "data_query",
        "snapshot_id": "sha256:snapshot",
    }
    message = ToolMessage(
        content="labels",
        name="publish_query_labels",
        tool_call_id="labels-1",
        id="labels-result",
        artifact=artifact,
    )

    serialized = DeerFlowClient._serialize_message(message)
    event = DeerFlowClient._tool_message_event(message)

    assert serialized["artifact"] == artifact
    assert event.data["artifact"] == artifact
