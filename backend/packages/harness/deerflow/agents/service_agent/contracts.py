
# TDD: 通用业务状态
from typing import TYPE_CHECKING, Any, Callable, Protocol, TypeAlias, TypedDict

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool


class ServiceState(TypedDict, total=False):
    """通用业务状态"""

    service_name: str
    version: str


StateMerger: TypeAlias = Callable[
    [ServiceState | None, ServiceState],
    ServiceState | None,
]


class ServiceAbilityAdapter(Protocol):
    """业务能力适配器协议。"""

    name: str

    def filter_tools(self, tools: list[BaseTool]) -> list[BaseTool]: 
        """过滤业务不允许使用的工具"""
        ...

    def build_tools(self) -> list[BaseTool]:
        """构建业务工具"""
        ...

    def build_middlewares(self) -> list[AgentMiddleware]: 
        """构建业务中间件"""
        ...

    def public_metadata(self) -> dict[str, Any]: 
        """构建业务中间件"""
        ...
