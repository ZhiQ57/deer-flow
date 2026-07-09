"""字段值索引服务装配函数。"""

from __future__ import annotations

from ....providers.normalizers import TextNormalizer
from ....providers.alias import AliasProvider
from ...connections.base import ConnectionProvider
from ....configs import TableRAGConfig
from .postgres.source_reader import PostgresSourceValueReader
from .postgres.value_index_store import PostgresValueIndexStore
from .sync_service import SyncFieldValueIndexService


def build_sync_value_index_service(
    config: TableRAGConfig,
    source_connection_provider: ConnectionProvider,
    index_connection_provider: ConnectionProvider,
    normalizer: TextNormalizer | None = None,
    alias_provider: AliasProvider | None = None,
) -> tuple[SyncFieldValueIndexService, PostgresValueIndexStore]:
    """根据配置装配字段值索引服务。

    Args:
        config: TableRAG 检索模块总配置。
        source_connection_provider: 外部注入的业务源库连接提供器。
        index_connection_provider: 外部注入的索引库连接提供器。
        normalizer: 可选文本归一化器。
        alias_provider: 可选字段值别名提供器。

    Returns:
        二元组，包含业务服务和 PostgreSQL 索引存储。
    """
    store = PostgresValueIndexStore(index_connection_provider, config.index_store)
    reader = PostgresSourceValueReader(source_connection_provider)
    service = SyncFieldValueIndexService(
        config=config,
        source_reader=reader,
        index_store=store,
        normalizer=normalizer,
        alias_provider=alias_provider,
    )
    return service, store
