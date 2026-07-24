"""TableRAG MCP 工具服务实现。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from ..configs import TableRAGConfig
from ..providers import EmbeddingProvider
from ..retrievers import HybridRetriever, normalize_retrieval_keywords
from ..runtime import TableRAGRuntime, build_table_rag_runtime
from ..pipeline import HybridRetrievalPipeline
from .connections import PsycopgConnectionProvider
from .options import build_retrieval_options
from .serialization import to_jsonable
from .settings import TableRAGMCPSettings


class TableRAGMCPOperation(StrEnum):
    """统一 MCP 工具支持的六类检索方法。

    方法值必须直接表达检索意图，供智能体在工具 schema 中选择：

    - `hybrid-search`：解析查询，召回 Evidence、表、列、字段值和 Join Graph，融合并重排序，补全 Join Graph 表候选后返回最终上下文。
    - `search-evidences`：使用 `queries` 中的独立关键词并行检索业务口径、指标定义和 SQL 生成约束，再按 RRF、关键词覆盖率和稳定 key 去重融合。
    - `search-tables`：使用 `queries` 中的独立关键词并行检索候选表，再按 RRF、关键词覆盖率和表名去重融合。
    - `search-columns`：使用 `queries` 中的独立关键词并行检索候选列、指标、维度、过滤字段和 Join Key，再按 RRF、关键词覆盖率和表列名去重融合。
    - `search-values`：使用 `queries` 中的独立关键词并行检索真实字段值、实体、别名、状态和分类值，再按 RRF、关键词覆盖率和表列值去重融合。
    - `expand-join-graph`：根据已知候选表扩展 Join Graph 路径。
    """

    HYBRID_SEARCH = "hybrid-search"
    SEARCH_EVIDENCES = "search-evidences"
    SEARCH_TABLES = "search-tables"
    SEARCH_COLUMNS = "search-columns"
    SEARCH_VALUES = "search-values"
    EXPAND_JOIN_GRAPH = "expand-join-graph"


MAX_MCP_KEYWORD_QUERIES = 8
"""MCP 单次单路检索允许的最大独立关键词数量。"""


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
        index_provider = PsycopgConnectionProvider(
            dsn=index_dsn,
            connect_timeout=self.config.database.index_database.connect_timeout,
        )
        self.runtime = build_table_rag_runtime(
            config=self.config,
            index_connection_provider=index_provider,
            embedding_provider=self.embedding_provider,
        )

    def execute(
        self,
        operation: TableRAGMCPOperation | str,
        *,
        query: str | None = None,
        queries: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """执行统一 MCP 工具指定的操作。

        Args:
            operation: 六类纯检索操作之一。
            query: 仅供 hybrid-search 使用的完整自然语言问题。
            queries: 仅供四类 search-* 使用的独立关键词或短语列表。
            **kwargs: top-k、Join Graph 跳数和表列过滤参数。

        Returns:
            JSON 友好的统一成功或错误结构。
        """
        try:
            resolved_operation = TableRAGMCPOperation(operation)
        except ValueError:
            supported = ", ".join(item.value for item in TableRAGMCPOperation)
            return self._failure(str(operation), ValueError(f"operation must be one of: {supported}"))

        return self._guard(
            resolved_operation.value,
            lambda: self._execute_operation(
                resolved_operation,
                query=query,
                queries=queries,
                **kwargs,
            ),
        )

    def _execute_operation(
        self,
        operation: TableRAGMCPOperation,
        *,
        query: str | None,
        queries: Sequence[str] | None,
        **kwargs: Any,
    ) -> Any:
        """按操作类型调用对应 SDK 能力。

        Args:
            operation: 已校验的操作类型。
            query: hybrid-search 使用的完整自然语言问题。
            queries: search-* 使用的独立关键词或短语列表。
            **kwargs: 标准检索参数。

        Returns:
            对应 SDK 操作的原始结果。
        """
        options = self._options(**kwargs)
        if operation is TableRAGMCPOperation.EXPAND_JOIN_GRAPH:
            if query is not None or queries is not None:
                raise ValueError("expand-join-graph only accepts table_names; query and queries are not supported")
            table_names = options.table_names
            if not table_names:
                raise ValueError("table_names is required for expand-join-graph")
            retriever = self._get_raw_retriever().join_graph_retriever
            return retriever.expand_paths(table_names, options) if retriever else []

        if operation is TableRAGMCPOperation.HYBRID_SEARCH:
            if queries is not None:
                raise ValueError("queries is not supported for hybrid-search; use the complete question in query")
            required_query = self._require_query(query, operation)
            return self._get_pipeline().retrieve(required_query, options)

        if query is not None:
            raise ValueError(f"query is not supported for {operation.value}; use independent keywords in queries")
        required_queries = self._require_queries(queries, operation)

        raw_retriever = self._get_raw_retriever()
        if operation is TableRAGMCPOperation.SEARCH_EVIDENCES:
            retriever = raw_retriever.evidence_retriever
            return retriever.search_evidences_keylist(required_queries, options) if retriever else []
        if operation is TableRAGMCPOperation.SEARCH_TABLES:
            retriever = raw_retriever.table_retriever
            return retriever.search_tables_keylist(required_queries, options) if retriever else []
        if operation is TableRAGMCPOperation.SEARCH_COLUMNS:
            retriever = raw_retriever.column_retriever
            return retriever.search_columns_keylist(required_queries, options) if retriever else []

        if operation is TableRAGMCPOperation.SEARCH_VALUES:
            retriever = raw_retriever.value_retriever
            return retriever.search_values_keylist(required_queries, options) if retriever else []
        raise AssertionError(f"unsupported operation: {operation.value}")

    @staticmethod
    def _require_query(query: str | None, operation: TableRAGMCPOperation) -> str:
        """校验检索操作必须提供非空查询。

        Args:
            query: 待校验查询。
            operation: 当前操作类型。

        Returns:
            校验通过的原始查询。
        """
        if query is None or not query.strip():
            raise ValueError(f"query is required for {operation.value}")
        return query

    @staticmethod
    def _require_queries(queries: Sequence[str] | None, operation: TableRAGMCPOperation) -> list[str]:
        """校验单路检索所需的独立关键词列表。

        Args:
            queries: 每项独立执行一次检索的关键词或短语。
            operation: 当前单路检索操作。

        Returns:
            去空白、去重后的关键词列表。
        """
        if queries is None:
            raise ValueError(f"queries is required for {operation.value}")
        if isinstance(queries, (str, bytes)):
            raise ValueError(f"queries must be a list of independent keywords for {operation.value}")
        if len(queries) > MAX_MCP_KEYWORD_QUERIES:
            raise ValueError(
                f"queries supports at most {MAX_MCP_KEYWORD_QUERIES} independent keywords for {operation.value}"
            )
        clean_queries = normalize_retrieval_keywords(queries)
        if not clean_queries:
            raise ValueError(f"queries must contain at least one non-empty keyword for {operation.value}")
        return clean_queries

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
            return self._failure(operation, exc)
        return {
            "ok": True,
            "operation": operation,
            "result": to_jsonable(result),
        }

    @staticmethod
    def _failure(operation: str, error: Exception) -> dict[str, Any]:
        """构造统一失败返回结构。

        Args:
            operation: 操作名称。
            error: 已捕获异常。

        Returns:
            JSON 友好的错误结构。
        """
        return {
            "ok": False,
            "operation": operation,
            "error": {
                "type": error.__class__.__name__,
                "message": str(error),
            },
        }
