"""TableRAG MCP Server 命令行入口。"""

from __future__ import annotations

import argparse

from .server import run_server
from .settings import TableRAGMCPSettings


def main() -> None:
    """解析命令行参数并启动 MCP 服务。"""
    base = TableRAGMCPSettings.from_env()
    parser = argparse.ArgumentParser(description="Run the TableRAG MCP server.")
    parser.add_argument("--config", default=None, help="TableRAG YAML/JSON 配置文件路径。")
    parser.add_argument("--index-dsn", default=None, help="索引库 PostgreSQL DSN。")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default=None)
    parser.add_argument("--host", default=None, help="HTTP 传输监听地址。")
    parser.add_argument("--port", type=int, default=None, help="HTTP 传输监听端口。")
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    parser.add_argument("--debug", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()

    settings = base.with_overrides(
        config_path=args.config,
        index_dsn=args.index_dsn,
        transport=args.transport,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        debug=args.debug,
    )
    run_server(settings)


if __name__ == "__main__":
    main()
