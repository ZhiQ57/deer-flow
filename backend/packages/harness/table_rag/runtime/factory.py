"""TableRAG 运行时装配工厂。"""

from __future__ import annotations

from ..configs import TableRAGConfig
from ..providers.alias import AliasProvider
from ..providers.embedding import EmbeddingProvider
from ..providers.normalizers import TextNormalizer
from ..providers.rerank import RerankProviderLike
from .connections.base import ConnectionProvider
from .context import TableRAGRuntime


def build_table_rag_runtime(
    config: TableRAGConfig,
    index_connection_provider: ConnectionProvider,
    source_connection_provider: ConnectionProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    rerank_provider: RerankProviderLike | None = None,
    normalizer: TextNormalizer | None = None,
    alias_provider: AliasProvider | None = None,
) -> TableRAGRuntime:
    """装配 TableRAG 高层运行时。

    Args:
        config: TableRAG 总配置。
        index_connection_provider: 外部注入的索引库连接提供器。
        source_connection_provider: 可选业务源库连接提供器。
        embedding_provider: 可选查询向量生成器。
        rerank_provider: 可选外部重排序服务。
        normalizer: 可选文本归一化器。
        alias_provider: 可选字段值别名提供器。

    Returns:
        TableRAG 运行时对象。
    """
    return TableRAGRuntime(
        config=config,
        index_connection_provider=index_connection_provider,
        source_connection_provider=source_connection_provider,
        embedding_provider=embedding_provider,
        rerank_provider=rerank_provider,
        normalizer=normalizer,
        alias_provider=alias_provider,
    )
