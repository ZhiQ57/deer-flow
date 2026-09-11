"""Single source of truth for the MCP-tool metadata tag.

A tool is "MCP-sourced" when it carries the ``deerflow_mcp`` metadata flag.
The tag is *written* where MCP tools are loaded (``tools.py``) and *read* by
deferred-tool assembly (``tool_search.py``) and the agent build site
(``agent.py``). Keeping the key, the tagger, and the predicate here means the
magic string lives in exactly one place, and readers import a public predicate
instead of a private cross-module helper.

This is a leaf module by design: it depends only on ``BaseTool`` so that any
module (including the tool loader) can import it without an import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.tools import BaseTool

MCP_TOOL_METADATA_KEY = "deerflow_mcp"
MCP_TOOL_ROUTING_METADATA_KEY = "deerflow_mcp_routing"


def tag_mcp_tool(tool: BaseTool) -> BaseTool:
    """Mark ``tool`` as MCP-sourced. Mutates in place and returns it for chaining."""
    tool.metadata = {**(tool.metadata or {}), MCP_TOOL_METADATA_KEY: True}
    return tool


def is_mcp_tool(tool: BaseTool) -> bool:
    """True when ``tool`` carries the MCP-source tag written by :func:`tag_mcp_tool`."""
    return (getattr(tool, "metadata", None) or {}).get(MCP_TOOL_METADATA_KEY) is True


def filter_mcp_tools(tools: list[BaseTool], allowed_names: list[str] | None) -> list[BaseTool]:
    """按 custom-agent 的通用 MCP 工具白名单过滤工具。

    Args:
        tools: 已加载的全部工具。
        allowed_names: None 表示继承全部 MCP；空列表表示禁用 MCP；
            其他值按工具最终暴露名称精确匹配。

    Returns:
        保留非 MCP 工具及白名单内 MCP 工具的新列表。
    """
    if allowed_names is None:
        return tools
    allowed = {name.strip() for name in allowed_names if isinstance(name, str) and name.strip()}
    if "*" in allowed:
        return tools
    return [tool for tool in tools if not is_mcp_tool(tool) or tool.name in allowed]


def tag_mcp_routing(tool: BaseTool, routing: Mapping[str, Any]) -> BaseTool:
    """Attach serialized MCP routing metadata to ``tool``."""
    tool.metadata = {
        **(tool.metadata or {}),
        MCP_TOOL_ROUTING_METADATA_KEY: dict(routing),
    }
    return tool


def get_mcp_routing(tool: BaseTool) -> dict[str, Any] | None:
    """Return routing metadata only for MCP tools whose routing mode is active."""
    if not is_mcp_tool(tool):
        return None
    routing = (getattr(tool, "metadata", None) or {}).get(MCP_TOOL_ROUTING_METADATA_KEY)
    if not isinstance(routing, dict) or routing.get("mode") == "off":
        return None
    return routing
