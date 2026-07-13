"""DataAgent 图工厂常量。"""

from __future__ import annotations

DATA_AGENT_NAME = "data-agent"

DATA_AGENT_SKILLS: frozenset[str] = frozenset(
    {
        "table-rag-agent",
        "data-analysis",
        "chart-visualization",
    }
)

# DataAgent 默认采用最小权限工具面：只保留 DeerFlow 内置框架工具、
# 只读 TableRAG MCP 和 DataAgent 专用 SQL/图表工具。
# 仅开放只读文件组供 DeerFlow 原生 Skill 渐进加载使用；后续还会经过
# DATA_AGENT_SAFE_LOCAL_TOOL_NAMES 二次过滤，不会暴露 ls/search/write/bash。
DATA_AGENT_TOOL_GROUPS: list[str] = ["file:read"]

DATA_AGENT_SAFE_LOCAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "ask_clarification",
        "present_files",
        "read_file",
        "task",
        "view_image",
    }
)
