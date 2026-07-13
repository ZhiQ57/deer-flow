"""DeerFlow 实验性业务智能体集合。"""

__all__ = [
    "DataAgentState",
    "build_data_agent",
    "make_data_agent",
]


def __getattr__(name: str):
    """延迟加载 DataAgent 图工厂和状态类型。

    Args:
        name: 请求的公开属性名。

    Return:
        对应公开对象。

    Raises:
        AttributeError: 属性不属于实验性 agents 公共入口。
    """
    if name in {"build_data_agent", "make_data_agent"}:
        from agents.data_agent import build_data_agent, make_data_agent

        exports = {
            "build_data_agent": build_data_agent,
            "make_data_agent": make_data_agent,
        }
        globals().update(exports)
        return exports[name]
    if name == "DataAgentState":
        from agents.thread_state import DataAgentState

        globals()[name] = DataAgentState
        return DataAgentState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
