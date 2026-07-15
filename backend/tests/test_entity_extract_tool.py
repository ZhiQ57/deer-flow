from __future__ import annotations

import json
from importlib import import_module
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, ToolMessage

from deerflow.tools import entity_extract_tool
from deerflow.tools.builtins.entity_extract_tool import normalize_alias_map

entity_tool_module = import_module("deerflow.tools.builtins.entity_extract_tool")


def test_entity_extract_alias_map_normalization_is_preserved() -> None:
    """校验保留实体抽取工具的业务别名输入校验。

    Args:
        无。

    Returns:
        None。
    """
    assert normalize_alias_map({" 黑金 ": " 高价值会员 "}) == {"黑金": "高价值会员"}


def test_entity_extract_tool_reads_real_user_message_and_returns_artifact(monkeypatch) -> None:
    """校验标准工具忽略隐藏消息，并通过 ToolMessage artifact 返回完整结果。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Returns:
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
        tool_call_id="call-1",
    )

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
    command = entity_extract_tool.func(runtime)

    assert entity_extract_tool.name == "entity_extract_tool"
    assert entity_extract_tool.args == {}
    assert set(command.update) == {"messages"}
    message = command.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.status == "success"
    assert message.name == "entity_extract_tool"
    assert message.artifact["original_query"] == "统计本月华东 GMV 和黑金客户"
    assert "成交总额" in message.artifact["normalized_query"]
    assert "高价值会员" in message.artifact["normalized_query"]
    assert json.loads(message.content) == {"ok": True, "query_context": message.artifact}


def test_entity_extract_tool_returns_controlled_error_without_user_message() -> None:
    """校验缺少真实用户消息时工具返回结构化错误。

    Args:
        无。

    Returns:
        None。
    """
    runtime = SimpleNamespace(
        state={"messages": []},
        context={},
        config={},
        tool_call_id="call-2",
    )

    command = entity_extract_tool.func(runtime)

    message = command.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert message.artifact is None
    assert json.loads(message.content)["ok"] is False
