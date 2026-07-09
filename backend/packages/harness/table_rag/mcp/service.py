"""TableRAG MCP 工具服务实现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..configs import TableRAGConfig
from ..providers import EmbeddingProvider
from ..retrievers import HybridRetriever
from ..runtime import TableRAGRuntime, build_table_rag_runtime
from ..pipeline import HybridRetrievalPipeline
from .connections import PsycopgConnectionProvider
from .options import build_retrieval_options
from .serialization import to_jsonable
from .settings import TableRAGMCPSettings


@dataclass
class TableRAGMCPService:
    """MCP 工具背后的 TableRAG 运行时服务。"""

    settings: TableRAGMCPSettings
    embedding_provider: EmbeddingProvider | None = None
    config: TableRAGConfig = field(init=False)
    runtime: TableRAGRuntime = field(init=False)
    _pipeline: HybridRetrievalPipeline | None = field(default=None, init=False)
    _raw_retriever: HybridRetriever | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """读取配置并装配运行时。"""
        self.config = self._load_config()
        index_dsn = self.settings.index_dsn or self.config.database.require_index_dsn()
        source_dsn = self.settings.source_dsn or self.config.database.source_database.dsn or index_dsn
        index_provider = PsycopgConnectionProvider(
            dsn=index_dsn,
            connect_timeout=self.config.database.index_database.connect_timeout,
        )
        source_provider = PsycopgConnectionProvider(
            dsn=source_dsn,
            connect_timeout=self.config.database.source_database.connect_timeout,
        )
        self.runtime = build_table_rag_runtime(
            config=self.config,
            index_connection_provider=index_provider,
            source_connection_provider=source_provider,
            embedding_provider=self.embedding_provider,
        )

    def retrieve(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """执行完整混合检索 Pipeline。"""
        return self._guard(
            "tablerag_retrieve",
            lambda: self._get_pipeline().retrieve(query, self._options(**kwargs)),
        )

    def raw_retrieve(self, query: str, schema_query: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """执行 raw 混合召回。"""
        return self._guard(
            "tablerag_raw_retrieve",
            lambda: self._get_raw_retriever().retrieve(query, self._options(**kwargs), schema_query=schema_query),
        )

    def search_evidences(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """执行 Evidence 单路召回。"""
        return self._guard(
            "tablerag_search_evidences",
            lambda: self._get_raw_retriever().evidence_retriever.search_evidences(query, self._options(**kwargs))
            if self._get_raw_retriever().evidence_retriever
            else [],
        )

    def search_tables(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """执行表结构单路召回。"""
        return self._guard(
            "tablerag_search_tables",
            lambda: self._get_raw_retriever().table_retriever.search_tables(query, self._options(**kwargs))
            if self._get_raw_retriever().table_retriever
            else [],
        )

    def search_columns(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """执行列字段单路召回。"""
        return self._guard(
            "tablerag_search_columns",
            lambda: self._get_raw_retriever().column_retriever.search_columns(query, self._options(**kwargs))
            if self._get_raw_retriever().column_retriever
            else [],
        )

    def search_values(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """执行字段值单路召回。"""
        return self._guard(
            "tablerag_search_values",
            lambda: self._get_raw_retriever().value_retriever.search_values(query, self._options(**kwargs))
            if self._get_raw_retriever().value_retriever
            else [],
        )

    def expand_join_graph(self, table_names: list[str], join_max_hops: int = 2) -> dict[str, Any]:
        """根据候选表扩展 Join Graph。"""
        return self._guard(
            "tablerag_expand_join_graph",
            lambda: self._get_raw_retriever().join_graph_retriever.expand_paths(
                table_names,
                self._options(join_max_hops=join_max_hops),
            )
            if self._get_raw_retriever().join_graph_retriever
            else [],
        )

    def validate_index(self) -> dict[str, Any]:
        """校验索引库连接和索引结构。"""
        return self._guard("tablerag_validate_index", self.runtime.validate_index_connection)

    def initialize_indexes(self) -> dict[str, Any]:
        """显式初始化索引结构，默认受权限开关保护。"""
        if not self.settings.allow_initialize_indexes:
            return self._permission_denied("tablerag_initialize_indexes", "Set TABLERAG_MCP_ALLOW_INITIALIZE=true")
        return self._guard("tablerag_initialize_indexes", self.runtime.initialize_indexes)

    def sync_field_values(self) -> dict[str, Any]:
        """同步字段值索引，默认受权限开关保护。"""
        if not self.settings.allow_sync_values:
            return self._permission_denied("tablerag_sync_field_values", "Set TABLERAG_MCP_ALLOW_SYNC_VALUES=true")

        def _sync() -> Any:
            service, _ = self.runtime.build_sync_value_index_service()
            return service.sync_all_report()

        return self._guard("tablerag_sync_field_values", _sync)

    def _load_config(self) -> TableRAGConfig:
        """加载 TableRAG 配置。"""
        if self.settings.config_path:
            return TableRAGConfig.from_file(self.settings.config_path)
        return TableRAGConfig()

    def _get_pipeline(self) -> HybridRetrievalPipeline:
        """延迟装配完整 Pipeline。"""
        if self._pipeline is None:
            self._pipeline = self.runtime.build_hybrid_retrieval_pipeline()
        return self._pipeline

    def _get_raw_retriever(self) -> HybridRetriever:
        """延迟装配 raw 混合召回器。"""
        if self._raw_retriever is None:
            self._raw_retriever = self.runtime.build_hybrid_retriever()
        return self._raw_retriever

    def _options(self, **kwargs: Any):
        """构造受 MCP 服务限制保护的检索参数。"""
        return build_retrieval_options(
            max_top_k=self.settings.max_top_k,
            max_join_hops=self.settings.max_join_hops,
            **kwargs,
        )

    def _guard(self, operation: str, action: Callable[[], Any]) -> dict[str, Any]:
        """执行工具逻辑并统一返回成功或错误结构。"""
        try:
            result = action()
        except Exception as exc:
            return {
                "ok": False,
                "operation": operation,
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
            }
        return {
            "ok": True,
            "operation": operation,
            "result": to_jsonable(result),
        }

    def _permission_denied(self, operation: str, hint: str) -> dict[str, Any]:
        """返回权限不足错误。"""
        return {
            "ok": False,
            "operation": operation,
            "error": {
                "type": "PermissionError",
                "message": f"{operation} is disabled for this MCP server. {hint}.",
            },
        }
