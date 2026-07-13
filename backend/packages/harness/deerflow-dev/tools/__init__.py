"""DataAgent 实验性工具能力。"""

__all__ = ["get_data_agent_tools"]


def __getattr__(name: str):
    """延迟加载 DataAgent 工具注册入口。

    Args:
        name: 请求的公开属性名。

    Return:
        DataAgent built-in tool 列表工厂。

    Raises:
        AttributeError: 属性不属于实验性 tools 公共入口。
    """
    if name == "get_data_agent_tools":
        from tools.builtins import get_data_agent_tools

        globals()[name] = get_data_agent_tools
        return get_data_agent_tools
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
