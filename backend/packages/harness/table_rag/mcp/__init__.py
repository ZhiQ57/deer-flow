"""TableRAG MCP 服务适配入口。"""

from .server import create_mcp_server, run_server
from .settings import TableRAGMCPSettings

__all__ = [
    "TableRAGMCPSettings",
    "create_mcp_server",
    "run_server",
]
