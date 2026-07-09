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
    parser.add_argument("--source-dsn", default=None, help="业务源库 PostgreSQL DSN。")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default=None)
    parser.add_argument("--host", default=None, help="HTTP 传输监听地址。")
    parser.add_argument("--port", type=int, default=None, help="HTTP 传输监听端口。")
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    parser.add_argument("--debug", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--allow-initialize-indexes", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--allow-sync-values", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()

    settings = base.with_overrides(
        config_path=args.config,
        index_dsn=args.index_dsn,
        source_dsn=args.source_dsn,
        transport=args.transport,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        debug=args.debug,
        allow_initialize_indexes=args.allow_initialize_indexes,
        allow_sync_values=args.allow_sync_values,
    )
    run_server(settings)


if __name__ == "__main__":
    main()
