"""统一检索器装配工厂。"""

from __future__ import annotations

from typing import Any

from ..configs import (
    ColumnRetrievalSettings,
    EvidenceRetrievalSettings,
    IndexStoreSettings,
    JoinGraphRetrievalSettings,
    RetrievalSettings,
    TableRAGConfig,
    TableRetrievalSettings,
)
from ..providers.embedding import EmbeddingProvider
from ..utils.database_type import SUPPORTED_RETRIEVER_DATABASES, UnsupportedRetrieverDatabaseError, normalize_database_type
from .base import (
    ColumnRetrieverBase,
    EvidenceRetrieverBase,
    JoinGraphRetrieverBase,
    TableRetrieverBase,
    ValueRetrieverBase,
)
from .hybrid_retriever import HybridRetriever


def build_evidence_retriever(
    connection_provider: Any,
    config: TableRAGConfig | None = None,
    database_type: str | None = None,
    index_store: IndexStoreSettings | None = None,
    retrieval_settings: EvidenceRetrievalSettings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> EvidenceRetrieverBase:
    """按数据库类型装配 Evidence 检索器。

    Args:
        connection_provider: 外部注入的索引库连接提供器。
        config: 可选 TableRAG 总配置；为空时使用默认配置。
        database_type: 可选数据库类型；为空时优先使用 config.database_type。
        index_store: 可选索引存储配置；传入时覆盖 config.index_store。
        retrieval_settings: 可选 Evidence 召回配置；传入时覆盖 config.retrieval.evidence。
        embedding_provider: 可选查询向量生成器。

    Returns:
        Evidence 检索器实例。
    """
    config = config or TableRAGConfig()
    store_settings = index_store or config.index_store
    evidence_retrieval = retrieval_settings or config.retrieval.evidence
    normalized_database_type = normalize_database_type(database_type or config.database_type)
    if normalized_database_type == "postgresql":
        from .postgresqls import PostgresEvidenceRetriever

        return PostgresEvidenceRetriever(
            connection_provider=connection_provider,
            index_store=store_settings,
            retrieval_settings=evidence_retrieval,
            embedding_provider=embedding_provider,
        )
    raise AssertionError("unreachable database type")


def build_table_retriever(
    connection_provider: Any,
    config: TableRAGConfig | None = None,
    database_type: str | None = None,
    index_store: IndexStoreSettings | None = None,
    retrieval_settings: TableRetrievalSettings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> TableRetrieverBase:
    """按数据库类型装配表结构检索器。

    Args:
        connection_provider: 外部注入的索引库连接提供器。
        config: 可选 TableRAG 总配置；为空时使用默认配置。
        database_type: 可选数据库类型；为空时优先使用 config.database_type。
        index_store: 可选索引存储配置；传入时覆盖 config.index_store。
        retrieval_settings: 可选表召回配置；传入时覆盖 config.retrieval.table。
        embedding_provider: 可选查询向量生成器。

    Returns:
        表结构检索器实例。
    """
    config = config or TableRAGConfig()
    store_settings = index_store or config.index_store
    table_retrieval = retrieval_settings or config.retrieval.table
    normalized_database_type = normalize_database_type(database_type or config.database_type)
    if normalized_database_type == "postgresql":
        from .postgresqls import PostgresTableIndexRetriever

        return PostgresTableIndexRetriever(
            connection_provider=connection_provider,
            index_store=store_settings,
            retrieval_settings=table_retrieval,
            embedding_provider=embedding_provider,
        )
    raise AssertionError("unreachable database type")


def build_column_retriever(
    connection_provider: Any,
    config: TableRAGConfig | None = None,
    database_type: str | None = None,
    index_store: IndexStoreSettings | None = None,
    retrieval_settings: ColumnRetrievalSettings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> ColumnRetrieverBase:
    """按数据库类型装配列字段检索器。

    Args:
        connection_provider: 外部注入的索引库连接提供器。
        config: 可选 TableRAG 总配置；为空时使用默认配置。
        database_type: 可选数据库类型；为空时优先使用 config.database_type。
        index_store: 可选索引存储配置；传入时覆盖 config.index_store。
        retrieval_settings: 可选列召回配置；传入时覆盖 config.retrieval.column。
        embedding_provider: 可选查询向量生成器。

    Returns:
        列字段检索器实例。
    """
    config = config or TableRAGConfig()
    store_settings = index_store or config.index_store
    column_retrieval = retrieval_settings or config.retrieval.column
    normalized_database_type = normalize_database_type(database_type or config.database_type)
    if normalized_database_type == "postgresql":
        from .postgresqls import PostgresColumnIndexRetriever

        return PostgresColumnIndexRetriever(
            connection_provider=connection_provider,
            index_store=store_settings,
            retrieval_settings=column_retrieval,
            embedding_provider=embedding_provider,
        )
    raise AssertionError("unreachable database type")


def build_value_retriever(
    connection_provider: Any,
    config: TableRAGConfig | None = None,
    database_type: str | None = None,
    index_store: IndexStoreSettings | None = None,
) -> ValueRetrieverBase:
    """按数据库类型装配字段值检索器。

    Args:
        connection_provider: 外部注入的索引库连接提供器。
        config: 可选 TableRAG 总配置；为空时使用默认配置。
        database_type: 可选数据库类型；为空时优先使用 config.database_type。
        index_store: 可选索引存储配置；传入时覆盖 config.index_store。

    Returns:
        字段值检索器实例。
    """
    config = config or TableRAGConfig()
    store_settings = index_store or config.index_store
    normalized_database_type = normalize_database_type(database_type or config.database_type)
    if normalized_database_type == "postgresql":
        from .postgresqls import PostgresValueIndexRetriever

        return PostgresValueIndexRetriever(connection_provider, store_settings)
    raise AssertionError("unreachable database type")


def build_join_graph_retriever(
    connection_provider: Any,
    config: TableRAGConfig | None = None,
    database_type: str | None = None,
    index_store: IndexStoreSettings | None = None,
    retrieval_settings: JoinGraphRetrievalSettings | None = None,
) -> JoinGraphRetrieverBase:
    """按数据库类型装配 Join Graph 检索器。

    Args:
        connection_provider: 外部注入的索引库连接提供器。
        config: 可选 TableRAG 总配置；为空时使用默认配置。
        database_type: 可选数据库类型；为空时优先使用 config.database_type。
        index_store: 可选索引存储配置；传入时覆盖 config.index_store。
        retrieval_settings: 可选 Join Graph 召回配置；传入时覆盖 config.retrieval.join_graph。

    Returns:
        Join Graph 检索器实例。
    """
    config = config or TableRAGConfig()
    store_settings = index_store or config.index_store
    join_graph_retrieval = retrieval_settings or config.retrieval.join_graph
    normalized_database_type = normalize_database_type(database_type or config.database_type)
    if normalized_database_type == "postgresql":
        from .postgresqls import PostgresJoinGraphRetriever

        return PostgresJoinGraphRetriever(connection_provider, store_settings, join_graph_retrieval)
    raise AssertionError("unreachable database type")


def build_hybrid_retriever(
    connection_provider: Any,
    config: TableRAGConfig | None = None,
    database_type: str | None = None,
    index_store: IndexStoreSettings | None = None,
    retrieval_settings: RetrievalSettings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> HybridRetriever:
    """按数据库类型装配 raw 混合检索器。

    Args:
        connection_provider: 外部注入的索引库连接提供器。
        config: 可选 TableRAG 总配置；为空时使用默认配置。
        database_type: 可选数据库类型；为空时优先使用 config.database_type。
        index_store: 可选索引存储配置；传入时覆盖 config.index_store。
        retrieval_settings: 可选在线检索配置；传入时覆盖 config.retrieval。
        embedding_provider: 可选查询向量生成器，由外部实现并注入。

    Returns:
        已按数据库类型装配好的 raw 混合检索器。
    """
    config = config or TableRAGConfig()
    store_settings = index_store or config.index_store
    retrieval = retrieval_settings or config.retrieval
    normalized_database_type = normalize_database_type(database_type or config.database_type)
    if normalized_database_type == "postgresql":
        return HybridRetriever(
            evidence_retriever=build_evidence_retriever(
                connection_provider=connection_provider,
                config=config,
                database_type=normalized_database_type,
                index_store=store_settings,
                retrieval_settings=retrieval.evidence,
                embedding_provider=embedding_provider,
            ),
            table_retriever=build_table_retriever(
                connection_provider=connection_provider,
                config=config,
                database_type=normalized_database_type,
                index_store=store_settings,
                retrieval_settings=retrieval.table,
                embedding_provider=embedding_provider,
            ),
            column_retriever=build_column_retriever(
                connection_provider=connection_provider,
                config=config,
                database_type=normalized_database_type,
                index_store=store_settings,
                retrieval_settings=retrieval.column,
                embedding_provider=embedding_provider,
            ),
            value_retriever=build_value_retriever(
                connection_provider=connection_provider,
                config=config,
                database_type=normalized_database_type,
                index_store=store_settings,
            ),
            join_graph_retriever=build_join_graph_retriever(
                connection_provider=connection_provider,
                config=config,
                database_type=normalized_database_type,
                index_store=store_settings,
                retrieval_settings=retrieval.join_graph,
            ),
        )
    raise AssertionError("unreachable database type")
