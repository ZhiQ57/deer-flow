"""MCP 服务使用的 PostgreSQL 连接提供器。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class PsycopgConnectionProvider:
    """基于 DSN 的 psycopg 连接提供器，仅供 MCP 适配层使用。"""

    dsn: str
    connect_timeout: int = 10

    @contextmanager
    def connect(self) -> Iterator[Any]:
        """创建一次性数据库连接上下文。

        Args:
            无。

        Returns:
            psycopg 数据库连接上下文。
        """
        if not self.dsn.strip():
            raise ValueError("PostgreSQL DSN must not be empty")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("TableRAG MCP PostgreSQL mode requires psycopg. Install table-rag[mcp].") from exc

        conn = psycopg.connect(self.dsn, connect_timeout=self.connect_timeout)
        try:
            yield conn
        finally:
            conn.close()
