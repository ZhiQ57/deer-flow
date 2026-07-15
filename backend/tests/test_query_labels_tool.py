from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from langchain.agents import AgentState, create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import Runnable

from deerflow.agents.middlewares.query_labels_middleware import QueryLabelsMiddleware
from deerflow.tools import publish_query_labels_tool


class _QueryLabelsState(AgentState):
    """查询标签真实 Agent 图集成测试状态。"""

    data_query_labels: dict[str, Any] | None
    data_retrieval_context: dict[str, Any] | None


class _ToolCallingModel(FakeMessagesListChatModel):
    """支持 create_agent 工具绑定的确定性测试模型。"""

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Runnable:
        """返回当前模型并使用预设工具调用响应。

        Args:
            tools: Agent 注册的工具。
            tool_choice: 可选工具选择约束。
            kwargs: 其他模型绑定参数。

        Returns:
            当前测试模型。
        """
        return self


def test_publish_query_labels_tool_exposes_structured_schema() -> None:
    """校验标签声明工具暴露稳定的结构化参数。

    Args:
        无。

    Returns:
        None。
    """
    schema = publish_query_labels_tool.args_schema.model_json_schema()

    assert publish_query_labels_tool.name == "publish_query_labels"
    assert publish_query_labels_tool.return_direct is False
    assert schema["required"] == ["intent", "labels"]
    assert set(schema["properties"]) == {"intent", "labels", "summary"}
    label_schema = schema["$defs"]["QueryLabelInput"]
    assert label_schema["required"] == ["label", "value", "source"]
    assert label_schema["properties"]["source"]["enum"] == ["user", "database", "derived"]


def test_publish_query_labels_placeholder_does_not_call_model() -> None:
    """校验标签工具占位实现只返回固定结果，不执行模型抽取。

    Args:
        无。

    Returns:
        None。
    """
    result = publish_query_labels_tool.func(
        intent="ranking",
        labels=[
            {
                "label": "指标",
                "value": "成交总额",
                "source": "user",
            }
        ],
        summary="查询成交总额排名",
    )

    assert result == "Query labels are processed by middleware."


def test_query_labels_middleware_runs_in_real_agent_graph_and_continues(monkeypatch) -> None:
    """校验真实 Agent 工具循环会拦截标签工具并继续生成最终回答。

    Args:
        monkeypatch: pytest monkeypatch fixture。

    Returns:
        None。
    """
    model = _ToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "publish_query_labels",
                        "id": "labels-call-1",
                        "args": {
                            "intent": "ranking",
                            "summary": "查询成交总额排名",
                            "labels": [
                                {
                                    "label": "指标",
                                    "value": "成交总额",
                                    "source": "user",
                                }
                            ],
                        },
                    }
                ],
            ),
            AIMessage(content="标签已展示，继续完成最终回答。"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[publish_query_labels_tool],
        middleware=[QueryLabelsMiddleware()],
        state_schema=_QueryLabelsState,
    )
    placeholder = MagicMock(side_effect=AssertionError("标签工具占位函数不应执行"))
    monkeypatch.setattr(publish_query_labels_tool, "func", placeholder)

    events = list(
        graph.stream(
            {"messages": [("user", "查询成交总额最高的商品")]},
            stream_mode=["values", "custom"],
        )
    )

    placeholder.assert_not_called()
    custom_events = [chunk for mode, chunk in events if mode == "custom"]
    assert custom_events == [
        {
            "type": "data_query_labels",
            "labels": {
                "intent": "ranking",
                "summary": "查询成交总额排名",
                "labels": [
                    {
                        "label": "指标",
                        "value": "成交总额",
                        "source": "user",
                    }
                ],
            },
        }
    ]

    final_state = [chunk for mode, chunk in events if mode == "values"][-1]
    assert final_state["data_query_labels"] == custom_events[0]["labels"]
    assert final_state["messages"][-1].content == "标签已展示，继续完成最终回答。"
    tool_messages = [message for message in final_state["messages"] if isinstance(message, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].artifact == final_state["data_query_labels"]
