"""DataAgent middleware 共用消息处理函数。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command

_SUMMARY_MESSAGE_NAME = "summary"


def is_visible_user_message(message: object) -> bool:
    """判断消息是否为用户侧真实可见输入。

    Args:
        message: LangChain 消息对象。

    Return:
        是真实用户消息则返回 True。
    """
    if not isinstance(message, HumanMessage):
        return False
    if message.name == _SUMMARY_MESSAGE_NAME:
        return False
    return not message.additional_kwargs.get("hide_from_ui")


def insert_after_leading_system_messages(messages: list[Any], injected: list[Any]) -> list[Any]:
    """把隐藏上下文插入到开头 SystemMessage 之后。

    Args:
        messages: 原始模型请求消息。
        injected: 需要注入的隐藏消息。

    Return:
        插入隐藏消息后的新消息列表。
    """
    index = 0
    while index < len(messages) and isinstance(messages[index], SystemMessage):
        index += 1
    return [*messages[:index], *injected, *messages[index:]]


def message_content_text(content: Any) -> str:
    """把 ToolMessage content 转换为文本。

    Args:
        content: ToolMessage 内容。

    Return:
        拼接后的文本。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content)


def result_messages(result: ToolMessage | Command) -> list[ToolMessage]:
    """读取工具结果中的 ToolMessage。

    Args:
        result: 工具执行结果。

    Return:
        ToolMessage 列表。
    """
    if isinstance(result, ToolMessage):
        return [result]
    if not isinstance(result.update, dict):
        return []
    messages = result.update.get("messages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, ToolMessage)]
