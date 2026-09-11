"""custom-agent MCP 工具白名单测试。"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

METADATA_PATH = Path(__file__).parents[1] / "packages" / "harness" / "deerflow" / "tools" / "mcp_metadata.py"
SPEC = importlib.util.spec_from_file_location("deerflow_mcp_metadata_test_module", METADATA_PATH)
assert SPEC is not None and SPEC.loader is not None
METADATA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = METADATA
SPEC.loader.exec_module(METADATA)

filter_mcp_tools = METADATA.filter_mcp_tools
tag_mcp_tool = METADATA.tag_mcp_tool


def test_filter_mcp_tools_keeps_non_mcp_and_allowlisted_tools() -> None:
    """白名单只限制 MCP 工具，不影响 DeerFlow 内置工具。"""
    builtin = SimpleNamespace(name="read_file", metadata={})
    sql_execute = tag_mcp_tool(SimpleNamespace(name="sql_execute", metadata={}))
    sqlrag = tag_mcp_tool(SimpleNamespace(name="sqlrag_retrieve", metadata={}))

    filtered = filter_mcp_tools([builtin, sql_execute, sqlrag], ["sqlrag_retrieve"])

    assert [tool.name for tool in filtered] == ["read_file", "sqlrag_retrieve"]


def test_filter_mcp_tools_empty_list_disables_mcp_only() -> None:
    """空列表表示禁用全部 MCP，但保留内置工具。"""
    builtin = SimpleNamespace(name="read_file", metadata={})
    sql_execute = tag_mcp_tool(SimpleNamespace(name="sql_execute", metadata={}))

    filtered = filter_mcp_tools([builtin, sql_execute], [])

    assert [tool.name for tool in filtered] == ["read_file"]
