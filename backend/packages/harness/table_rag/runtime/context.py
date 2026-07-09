"""TableRAG 运行时上下文。"""

from __future__ import annotations

from dataclasses import dataclass

from ..configs import TableRAGConfig
from ..providers.alias import AliasProvider
from ..providers.embedding import EmbeddingProvider
from ..providers.normalizers import TextNormalizer
from ..providers.rerank import RerankProviderLike
from ..pipeline import HybridRetrievalPipeline, build_hybrid_retrieval_pipeline
from ..retrievers import HybridRetriever, build_hybrid_retriever
from .backend_registry import get_runtime_backend
from .connections.base import ConnectionProvider
from .connections.validators import ConnectionValidationResult
from .indexing.index_tables import IndexInitializationResult
from .indexing.sync_values import SyncFieldValueIndexService, ValueIndexStore


@dataclass(frozen=True)
class TableRAGRuntime:
    """TableRAG 高层运行时入口，集中持有配置和外部连接提供器。"""

    config: TableRAGConfig
    index_connection_provider: ConnectionProvider
    source_connection_provider: ConnectionProvider | None = None
    embedding_provider: EmbeddingProvider | None = None
    rerank_provider: RerankProviderLike | None = None
    normalizer: TextNormalizer | None = None
    alias_provider: AliasProvider | None = None

    def build_hybrid_retriever(self) -> HybridRetriever:
        """装配 raw 混合检索器。

        Args:
            无。

        Returns:
            raw 混合检索器实例。
        """
        return build_hybrid_retriever(
            connection_provider=self.index_connection_provider,
            config=self.config,
            embedding_provider=self.embedding_provider,
        )

    def build_hybrid_retrieval_pipeline(self) -> HybridRetrievalPipeline:
        """装配混合检索 Pipeline。

        Args:
            无。

        Returns:
            混合检索 Pipeline 实例。
        """
        return build_hybrid_retrieval_pipeline(
            connection_provider=self.index_connection_provider,
            config=self.config,
            embedding_provider=self.embedding_provider,
            rerank_provider=self.rerank_provider,
        )

    def build_sync_value_index_service(self) -> tuple[SyncFieldValueIndexService, ValueIndexStore]:
        """装配字段值索引同步服务。

        Args:
            无。

        Returns:
            字段值索引服务和索引存储。
        """
        if self.source_connection_provider is None:
            raise ValueError("source_connection_provider is required to build value index service")
        backend = get_runtime_backend(self.config.database_type)
        return backend.build_sync_value_index_service(
            config=self.config,
            source_connection_provider=self.source_connection_provider,
            index_connection_provider=self.index_connection_provider,
            normalizer=self.normalizer,
            alias_provider=self.alias_provider,
        )

    def validate_index_connection(self) -> ConnectionValidationResult:
        """校验索引库连接和索引结构是否满足配置要求。

        Args:
            无。

        Returns:
            连接校验结果。
        """
        backend = get_runtime_backend(self.config.database_type)
        return backend.validate_connection(self.index_connection_provider, self.config)

    def initialize_indexes(self) -> IndexInitializationResult:
        """显式初始化当前数据库后端索引结构。

        Args:
            无。

        Returns:
            索引初始化结果。
        """
        backend = get_runtime_backend(self.config.database_type)
        return backend.initialize_indexes(self.config, self.index_connection_provider)
