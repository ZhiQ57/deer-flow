"""PostgreSQL 索引初始化实现。"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .....configs import IndexStoreSettings, TableRAGConfig
from ....connections.base import ConnectionProvider
from ..base import IndexInitializationResult, IndexInitializer
from .ddl import (
    build_column_index_statements,
    build_evidence_index_statements,
    build_join_graph_statements,
    build_postgres_index_statements,
    build_schema_index_statements,
    build_table_index_statements,
    build_value_index_statements,
)
from .requirements import required_extensions_for_config


class PostgresIndexInitializer(IndexInitializer):
    """PostgreSQL 索引初始化器，显式执行 TableRAG 索引 DDL。"""

    def __init__(self, connection_provider: ConnectionProvider):
        """初始化 PostgreSQL 索引初始化器。

        Args:
            connection_provider: 外部注入的索引库连接提供器。

        Returns:
            None。
        """
        self.connection_provider = connection_provider

    def initialize(self, config: TableRAGConfig) -> IndexInitializationResult:
        """根据配置初始化完整 PostgreSQL 索引结构。

        Args:
            config: TableRAG 总配置。

        Returns:
            索引初始化结果。
        """
        statements = build_postgres_index_statements(config)
        self.execute_statements(statements)
        return IndexInitializationResult(
            applied_statements=len(statements),
            created_extensions=sorted(required_extensions_for_config(config)),
            metadata={"database_type": "postgresql"},
        )

    def execute_statements(self, statements: Sequence[str]) -> int:
        """执行一组 DDL 语句。

        Args:
            statements: 待执行的 DDL 语句列表。

        Returns:
            实际执行的语句数量。
        """
        with self.connection_provider.connect() as conn:
            try:
                with conn.cursor() as cur:
                    for statement in statements:
                        cur.execute(statement)
                conn.commit()
            except Exception:
                # 初始化失败时尽量回滚，避免宿主连接处于异常事务状态。
                rollback = getattr(conn, "rollback", None)
                if callable(rollback):
                    rollback()
                raise
        return len(statements)


class PostgresIndexSchemaManager:
    """PostgreSQL 索引表管理器，提供按模块初始化入口。"""

    def __init__(self, connection_provider: ConnectionProvider):
        """初始化索引表管理器。

        Args:
            connection_provider: 外部注入的索引库连接提供器。

        Returns:
            None。
        """
        self.initializer = PostgresIndexInitializer(connection_provider)

    def create_all(self, config: TableRAGConfig) -> IndexInitializationResult:
        """创建完整 TableRAG PostgreSQL 索引表。

        Args:
            config: TableRAG 总配置。

        Returns:
            索引初始化结果。
        """
        return self.initializer.initialize(config)

    def create_schema_index_tables(self, settings: IndexStoreSettings) -> int:
        """创建 Evidence、表结构、列字段和 Join Graph 索引表。

        Args:
            settings: 索引存储配置。

        Returns:
            执行的 DDL 语句数量。
        """
        return self._execute(build_schema_index_statements, settings)

    def create_evidence_index(self, settings: IndexStoreSettings) -> int:
        """创建 Evidence 业务规则索引表和基础索引。

        Args:
            settings: 索引存储配置。

        Returns:
            执行的 DDL 语句数量。
        """
        return self._execute(build_evidence_index_statements, settings)

    def create_table_index(self, settings: IndexStoreSettings) -> int:
        """创建表结构索引表和基础索引。

        Args:
            settings: 索引存储配置。

        Returns:
            执行的 DDL 语句数量。
        """
        return self._execute(build_table_index_statements, settings)

    def create_column_index(self, settings: IndexStoreSettings) -> int:
        """创建列字段索引表和基础索引。

        Args:
            settings: 索引存储配置。

        Returns:
            执行的 DDL 语句数量。
        """
        return self._execute(build_column_index_statements, settings)

    def create_join_graph_index(self, settings: IndexStoreSettings) -> int:
        """创建 Schema Join Graph 边表。

        Args:
            settings: 索引存储配置。

        Returns:
            执行的 DDL 语句数量。
        """
        return self._execute(build_join_graph_statements, settings)

    def create_value_index_table(self, settings: IndexStoreSettings) -> int:
        """创建字段值索引表和基础索引。

        Args:
            settings: 索引存储配置。

        Returns:
            执行的 DDL 语句数量。
        """
        return self._execute(build_value_index_statements, settings)

    def _execute(self, builder: Callable, settings: IndexStoreSettings) -> int:
        """执行某个索引模块的 DDL 构建结果。

        Args:
            builder: DDL 构建函数。
            settings: 对应模块配置。

        Returns:
            执行的 DDL 语句数量。
        """
        return self.initializer.execute_statements(builder(settings))


def create_postgres_indexes(config: TableRAGConfig, connection_provider: ConnectionProvider) -> IndexInitializationResult:
    """按配置创建 PostgreSQL 索引表。

    Args:
        config: TableRAG 总配置。
        connection_provider: 外部注入的索引库连接提供器。

    Returns:
        索引初始化结果。
    """
    return PostgresIndexInitializer(connection_provider).initialize(config)
