"""TableRAG MCP 服务配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Mapping


_SUPPORTED_TRANSPORTS = {"stdio", "sse", "streamable-http"}
_SUPPORTED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass(frozen=True)
class TableRAGMCPSettings:
    """MCP 服务启动配置。"""

    config_path: str | None = None
    index_dsn: str | None = None
    source_dsn: str | None = None
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    streamable_http_path: str = "/mcp"
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    mount_path: str = "/"
    log_level: str = "INFO"
    debug: bool = False
    json_response: bool = False
    stateless_http: bool = False
    max_top_k: int = 100
    max_join_hops: int = 5
    allow_initialize_indexes: bool = False
    allow_sync_values: bool = False

    def __post_init__(self) -> None:
        """校验 MCP 服务配置。"""
        normalized_transport = self.transport.strip().lower()
        if normalized_transport not in _SUPPORTED_TRANSPORTS:
            raise ValueError("transport must be one of: " + ", ".join(sorted(_SUPPORTED_TRANSPORTS)))
        normalized_log_level = self.log_level.strip().upper()
        if normalized_log_level not in _SUPPORTED_LOG_LEVELS:
            raise ValueError("log_level must be one of: " + ", ".join(sorted(_SUPPORTED_LOG_LEVELS)))
        if self.port <= 0 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.max_top_k <= 0:
            raise ValueError("max_top_k must be a positive integer")
        if self.max_join_hops < 0:
            raise ValueError("max_join_hops must be a non-negative integer")
        object.__setattr__(self, "transport", normalized_transport)
        object.__setattr__(self, "log_level", normalized_log_level)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "TableRAGMCPSettings":
        """从环境变量读取 MCP 服务配置。

        Args:
            environ: 可选环境变量映射；测试可传入自定义字典。

        Returns:
            MCP 服务配置对象。
        """
        env = environ or os.environ
        return cls(
            config_path=_first(env, "TABLERAG_MCP_CONFIG", "TABLERAG_CONFIG"),
            index_dsn=_first(env, "TABLERAG_MCP_INDEX_DSN", "TABLERAG_INDEX_DSN"),
            source_dsn=_first(env, "TABLERAG_MCP_SOURCE_DSN", "TABLERAG_SOURCE_DSN"),
            transport=_first(env, "TABLERAG_MCP_TRANSPORT") or "stdio",
            host=_first(env, "TABLERAG_MCP_HOST") or "127.0.0.1",
            port=_int(env, "TABLERAG_MCP_PORT", 8000),
            streamable_http_path=_first(env, "TABLERAG_MCP_STREAMABLE_HTTP_PATH") or "/mcp",
            sse_path=_first(env, "TABLERAG_MCP_SSE_PATH") or "/sse",
            message_path=_first(env, "TABLERAG_MCP_MESSAGE_PATH") or "/messages/",
            mount_path=_first(env, "TABLERAG_MCP_MOUNT_PATH") or "/",
            log_level=_first(env, "TABLERAG_MCP_LOG_LEVEL") or "INFO",
            debug=_bool(env, "TABLERAG_MCP_DEBUG", False),
            json_response=_bool(env, "TABLERAG_MCP_JSON_RESPONSE", False),
            stateless_http=_bool(env, "TABLERAG_MCP_STATELESS_HTTP", False),
            max_top_k=_int(env, "TABLERAG_MCP_MAX_TOP_K", 100),
            max_join_hops=_int(env, "TABLERAG_MCP_MAX_JOIN_HOPS", 5),
            allow_initialize_indexes=_bool(env, "TABLERAG_MCP_ALLOW_INITIALIZE", False),
            allow_sync_values=_bool(env, "TABLERAG_MCP_ALLOW_SYNC_VALUES", False),
        )

    def with_overrides(self, **kwargs: object) -> "TableRAGMCPSettings":
        """返回覆盖部分字段后的配置副本。"""
        clean = {key: value for key, value in kwargs.items() if value is not None}
        return replace(self, **clean)


def _first(env: Mapping[str, str], *names: str) -> str | None:
    """按优先级读取第一个非空环境变量。"""
    for name in names:
        value = env.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    """读取整数环境变量。"""
    value = _first(env, name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    """读取布尔环境变量。"""
    value = _first(env, name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
