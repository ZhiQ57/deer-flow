"""运行时数据库后端注册表。"""

from __future__ import annotations

from typing import Protocol

from ..configs import TableRAGConfig
from ..providers.alias import AliasProvider
from ..providers.normalizers import TextNormalizer
from .connections.base import ConnectionProvider
from .connections.validators import ConnectionValidationResult
from .indexing.index_tables import IndexInitializationResult
from .indexing.sync_values.base import ValueIndexStore
from .indexing.sync_values.sync_service import SyncFieldValueIndexService


_POSTGRES_RUNTIME_ALIASES = {"postgresql", "postgres", "pg", "postgresqls"}


class UnsupportedRuntimeBackendError(ValueError):
    """未注册运行时数据库后端错误。"""


class RuntimeBackend(Protocol):
    """运行时数据库后端协议，封装校验、索引初始化和字段值同步装配。"""

    database_type: str

    def validate_connection(
        self,
        connection_provider: ConnectionProvider,
        config: TableRAGConfig,
    ) -> ConnectionValidationResult:
        """校验外部注入连接是否满足当前后端要求。

        Args:
            connection_provider: 外部连接提供器。
            config: TableRAG 总配置。

        Returns:
            连接校验结果。
        """

    def initialize_indexes(
        self,
        config: TableRAGConfig,
        connection_provider: ConnectionProvider,
    ) -> IndexInitializationResult:
        """显式初始化当前后端索引结构。

        Args:
            config: TableRAG 总配置。
            connection_provider: 外部索引库连接提供器。

        Returns:
            索引初始化结果。
        """

    def build_sync_value_index_service(
        self,
        config: TableRAGConfig,
        source_connection_provider: ConnectionProvider,
        index_connection_provider: ConnectionProvider,
        normalizer: TextNormalizer | None = None,
        alias_provider: AliasProvider | None = None,
    ) -> tuple[SyncFieldValueIndexService, ValueIndexStore]:
        """装配字段值索引同步服务。

        Args:
            config: TableRAG 总配置。
            source_connection_provider: 业务源库连接提供器。
            index_connection_provider: 索引库连接提供器。
            normalizer: 可选文本归一化器。
            alias_provider: 可选字段值别名提供器。

        Returns:
            字段值同步服务和字段值索引存储。
        """


class PostgresRuntimeBackend:
    """PostgreSQL 运行时后端实现。"""

    database_type = "postgresql"

    def validate_connection(
        self,
        connection_provider: ConnectionProvider,
        config: TableRAGConfig,
    ) -> ConnectionValidationResult:
        """校验 PostgreSQL 连接和索引结构。"""
        from .connections.postgresqls import validate_postgres_connection

        return validate_postgres_connection(connection_provider, config)

    def initialize_indexes(
        self,
        config: TableRAGConfig,
        connection_provider: ConnectionProvider,
    ) -> IndexInitializationResult:
        """初始化 PostgreSQL 索引结构。"""
        from .indexing.index_tables.postgres.initializer import create_postgres_indexes

        return create_postgres_indexes(config, connection_provider)

    def build_sync_value_index_service(
        self,
        config: TableRAGConfig,
        source_connection_provider: ConnectionProvider,
        index_connection_provider: ConnectionProvider,
        normalizer: TextNormalizer | None = None,
        alias_provider: AliasProvider | None = None,
    ) -> tuple[SyncFieldValueIndexService, ValueIndexStore]:
        """装配 PostgreSQL 字段值索引同步服务。"""
        from .indexing.sync_values import build_sync_value_index_service

        return build_sync_value_index_service(
            config=config,
            source_connection_provider=source_connection_provider,
            index_connection_provider=index_connection_provider,
            normalizer=normalizer,
            alias_provider=alias_provider,
        )


_RUNTIME_BACKENDS: dict[str, RuntimeBackend] = {}


def register_runtime_backend(backend: RuntimeBackend) -> None:
    """注册运行时后端。

    Args:
        backend: 运行时后端实现。

    Returns:
        None。
    """
    normalized_type = _normalize_runtime_database_type(backend.database_type)
    _RUNTIME_BACKENDS[normalized_type] = backend


def get_runtime_backend(database_type: str) -> RuntimeBackend:
    """按数据库类型获取运行时后端。

    Args:
        database_type: 外部配置数据库类型。

    Returns:
        运行时后端实现。
    """
    normalized_type = _normalize_runtime_database_type(database_type)
    try:
        return _RUNTIME_BACKENDS[normalized_type]
    except KeyError as exc:
        raise UnsupportedRuntimeBackendError(
            f"Unsupported runtime database backend: {database_type!r}. "
            f"Registered backends: {', '.join(sorted(_RUNTIME_BACKENDS))}"
        ) from exc


def _normalize_runtime_database_type(database_type: str) -> str:
    """归一化运行时后端类型，允许第三方注册非检索后端名称。

    Args:
        database_type: 外部配置或后端声明的数据库类型。

    Returns:
        运行时后端注册表键。
    """
    if not isinstance(database_type, str) or not database_type.strip():
        raise ValueError("runtime database_type must be a non-empty string")
    normalized = database_type.strip().lower().replace("-", "_")
    if normalized in _POSTGRES_RUNTIME_ALIASES:
        return "postgresql"
    return normalized


register_runtime_backend(PostgresRuntimeBackend())


__all__ = [
    "PostgresRuntimeBackend",
    "RuntimeBackend",
    "UnsupportedRuntimeBackendError",
    "get_runtime_backend",
    "register_runtime_backend",
]
