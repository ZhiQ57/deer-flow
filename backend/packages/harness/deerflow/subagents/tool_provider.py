"""子代理运行时工具提供器扩展点。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from langchain_core.tools import BaseTool


@dataclass(frozen=True, slots=True)
class SubagentToolContext:
    """子代理工具构建上下文。

    Args:
        subagent_type: 当前准备启动的子代理类型。
        thread_id: 父线程标识。
        run_id: 父运行标识。
        user_id: 当前认证用户标识。
        agent_name: 当前 Custom Agent 名称。
        parent_context: 父运行的只读运行上下文。
        parent_state: 父智能体当前只读状态。
        task_prompt: 当前 task 工具传给子代理的任务 prompt
    """

    subagent_type: str
    thread_id: str | None
    run_id: str | None
    user_id: str | None
    agent_name: str | None
    parent_context: Mapping[str, Any]
    parent_state: Mapping[str, Any]
    # ADD
    task_prompt: str | None = None


class SubagentToolProvider(Protocol):
    """应用层子代理工具提供器协议。"""

    def build_tools(self, context: SubagentToolContext) -> list[BaseTool]:
        """根据可信运行上下文构建子代理工具。

        Args:
            context: 子代理工具构建上下文。

        Returns:
            应注入当前子代理的工具列表。
        """


_PROVIDERS: dict[str, SubagentToolProvider] = {}
_PROVIDERS_LOCK = RLock()


def register_subagent_tool_provider(
    name: str,
    provider: SubagentToolProvider,
    *,
    replace: bool = False,
) -> None:
    """注册应用层子代理工具提供器。

    Args:
        name: 稳定的提供器名称。
        provider: 工具提供器实现。
        replace: 同名提供器存在时是否替换。

    Raises:
        ValueError: 名称为空或同名提供器已存在。
    """
    normalized = name.strip()
    if not normalized:
        raise ValueError("子代理工具提供器名称不能为空。")
    with _PROVIDERS_LOCK:
        if normalized in _PROVIDERS and not replace:
            raise ValueError(f"子代理工具提供器已存在：{normalized}")
        _PROVIDERS[normalized] = provider


def unregister_subagent_tool_provider(name: str) -> None:
    """删除指定子代理工具提供器。

    Args:
        name: 已注册的提供器名称。

    Returns:
        无返回值。
    """
    with _PROVIDERS_LOCK:
        _PROVIDERS.pop(name.strip(), None)


def build_registered_subagent_tools(context: SubagentToolContext) -> list[BaseTool]:
    """调用全部已注册提供器并汇总工具。

    Args:
        context: 当前子代理的可信构建上下文。

    Returns:
        按提供器注册顺序汇总的工具列表。
    """
    with _PROVIDERS_LOCK:
        providers = tuple(_PROVIDERS.values())
    tools: list[BaseTool] = []
    for provider in providers:
        tools.extend(provider.build_tools(context))
    return tools


def clear_subagent_tool_providers() -> None:
    """清空提供器注册表，供测试隔离使用。

    Returns:
        无返回值。
    """
    with _PROVIDERS_LOCK:
        _PROVIDERS.clear()
