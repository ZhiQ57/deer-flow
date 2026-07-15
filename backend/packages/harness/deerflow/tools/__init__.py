from .builtins.entity_extract_tool import entity_extract_tool
from .builtins.query_labels_tool import publish_query_labels_tool
from .tools import get_available_tools

__all__ = ["entity_extract_tool", "get_available_tools", "publish_query_labels_tool", "skill_manage_tool"]


def __getattr__(name: str):
    if name == "skill_manage_tool":
        from .skill_manage_tool import skill_manage_tool

        return skill_manage_tool
    raise AttributeError(name)
