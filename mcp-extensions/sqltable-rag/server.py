"""sqltable-rag 外部 MCP Server 启动包装。"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import yaml


def _resolve_template(value: str) -> str:
    """解析 `$VAR` 与 `${VAR:-default}` 配置模板。"""
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}", value.strip())
    if match:
        return os.getenv(match.group(1)) or match.group(2)
    expanded = os.path.expandvars(value)
    path = Path(expanded)
    return str(path if path.is_absolute() else Path.cwd() / path)


def _optional_port(value: object) -> int | None:
    """将可选端口值转换成整数。"""
    if value is None or value == "":
        return None
    return int(value)


def main() -> None:
    """加载扩展配置并启动现有 TableRAG MCP Server。"""
    parser = argparse.ArgumentParser(description="Run DeerFlow sqltable-rag MCP Server.")
    parser.add_argument("--config", default=os.getenv("SQLTABLE_RAG_CONFIG_PATH", str(Path(__file__).with_name("config.yaml"))))
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    server = raw.get("server") if isinstance(raw.get("server"), dict) else {}
    tablerag = raw.get("tablerag") if isinstance(raw.get("tablerag"), dict) else {}
    from table_rag.mcp import TableRAGMCPSettings, run_server

    configured_path = str(tablerag.get("config_path") or "tablerag.yaml")
    resolved_config_path = os.getenv("TABLERAG_MCP_CONFIG") or _resolve_template(
        configured_path,
    )
    settings = TableRAGMCPSettings.from_env().with_overrides(
        config_path=resolved_config_path,
        index_dsn=os.getenv(str(tablerag.get("index_dsn_env") or "TABLERAG_MCP_INDEX_DSN")),
        transport=args.transport or os.getenv("TABLERAG_MCP_TRANSPORT") or server.get("transport"),
        host=args.host or os.getenv("TABLERAG_MCP_HOST") or server.get("host"),
        port=args.port or _optional_port(os.getenv("TABLERAG_MCP_PORT")) or _optional_port(server.get("port")),
    )
    run_server(settings)


if __name__ == "__main__":
    main()
