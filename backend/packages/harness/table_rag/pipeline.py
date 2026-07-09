"""TableRAG 混合检索 Pipeline。"""

from __future__ import annotations

from typing import Any

from .configs import IndexStoreSettings, RetrievalSettings, TableRAGConfig
from .providers.embedding import EmbeddingProvider
from .providers.rerank import RerankProviderLike
from .query_parser import DefaultQueryParser, QueryParserBase
from .reranker import (
    RetrievalRerankerBase,
    build_ranking_pipeline,
    merge_table_hits,
    tables_from_join_graphs,
)
from .retrievers.base import (
    ColumnRetrieverBase,
    EvidenceRetrieverBase,
    JoinGraphRetrieverBase,
    TableRetrieverBase,
    ValueRetrieverBase,
)
from .retrievers.factory import build_hybrid_retriever
from .retrievers.hybrid_retriever import HybridRetriever
from .schemas import (
    HybridRetrievalResult,
    JoinGraphRetrievalResult,
    RetrievalOptions,
)
from .utils.database_type import SUPPORTED_RETRIEVER_DATABASES, UnsupportedRetrieverDatabaseError, normalize_database_type


class HybridRetrievalPipeline:
    """混合检索 Pipeline，编排查询解析、raw 混合召回、重排序和结果组装。"""

    def __init__(
        self,
        evidence_retriever: EvidenceRetrieverBase | None = None,
        table_retriever: TableRetrieverBase | None = None,
        column_retriever: ColumnRetrieverBase | None = None,
        value_retriever: ValueRetrieverBase | None = None,
        join_graph_retriever: JoinGraphRetrieverBase | None = None,
        hybrid_retriever: HybridRetriever | None = None,
        reranker: RetrievalRerankerBase | None = None,
        settings: RetrievalSettings | None = None,
        query_parser: QueryParserBase | None = None,
    ):
        """初始化混合检索 Pipeline。

        Args:
            evidence_retriever: 可选 Evidence 检索器实例。
            table_retriever: 可选表检索器实例。
            column_retriever: 可选列检索器实例。
            value_retriever: 可选字段值检索器实例。
            join_graph_retriever: 可选连接图检索器实例。
            hybrid_retriever: 可选 raw 混合检索器实例。
            reranker: 可选重排序器实例。
            settings: 默认在线检索和重排序配置。
            query_parser: 可选查询解析器，用于生成 schema 检索扩展文本。

        Returns:
            None。
        """
        self.hybrid_retriever = hybrid_retriever or HybridRetriever(
            evidence_retriever=evidence_retriever,
            table_retriever=table_retriever,
            column_retriever=column_retriever,
            value_retriever=value_retriever,
            join_graph_retriever=join_graph_retriever,
        )
        self.evidence_retriever = self.hybrid_retriever.evidence_retriever
        self.table_retriever = self.hybrid_retriever.table_retriever
        self.column_retriever = self.hybrid_retriever.column_retriever
        self.value_retriever = self.hybrid_retriever.value_retriever
        self.join_graph_retriever = self.hybrid_retriever.join_graph_retriever
        self.reranker = reranker or build_ranking_pipeline(settings)
        self.query_parser = query_parser or DefaultQueryParser()

    def retrieve(self, query: str, options: RetrievalOptions | None = None) -> HybridRetrievalResult:
        """执行混合检索 Pipeline。

        Args:
            query: 检索查询字符串。
            options: 检索选项；为空时使用默认值。

        Returns:
            混合检索结果。
        """
        options = options or RetrievalOptions()
        parsed_query = self.query_parser.parse(query)
        schema_query = parsed_query.schema_search_text
        raw_result = self.hybrid_retriever.retrieve(query, options, schema_query=schema_query)
        evidence_hits = raw_result.evidences
        table_hits = raw_result.tables
        column_hits = raw_result.columns
        value_hits = raw_result.values
        join_graphs = raw_result.join_graphs

        candidate_columns = self.reranker.rerank_columns(query, column_hits)[: options.final_column_top_k]
        candidate_tables = self.reranker.rerank_tables(query, table_hits, candidate_columns, value_hits)[
            : options.final_table_top_k
        ]

        final_tables = candidate_tables
        if join_graphs:
            final_tables = merge_table_hits(candidate_tables, tables_from_join_graphs(join_graphs))
            final_tables = self.reranker.rerank_tables(query, final_tables, candidate_columns, value_hits)[
                : options.final_table_top_k
            ]

        return HybridRetrievalResult(
            query=query,
            tables=final_tables,
            columns=candidate_columns,
            values=value_hits,
            join_graphs=join_graphs,
            evidences=evidence_hits,
            metadata={
                "parsed_query": {
                    "intent": parsed_query.intent,
                    "metrics": parsed_query.metrics,
                    "dimensions": parsed_query.dimensions,
                    "entities": parsed_query.entities,
                    "filters": parsed_query.filters,
                    "time_expressions": parsed_query.time_expressions,
                    "top_k": parsed_query.top_k,
                    "sort_direction": parsed_query.sort_direction,
                },
                "raw_retrieval": raw_result.metadata,
                "evidence_recall_count": len(evidence_hits),
                "table_recall_count": len(table_hits),
                "candidate_table_count": len(candidate_tables),
                "expanded_table_count": len({table for path in join_graphs for table in _tables_from_graph(path)}),
                "final_table_count": len(final_tables),
                "table_candidate_count": len(final_tables),
                "column_recall_count": len(column_hits),
                "value_recall_count": len(value_hits),
                "join_graph_count": len(join_graphs),
            },
        )


def _tables_from_graph(graph: JoinGraphRetrievalResult) -> list[str]:
    """从 Join Graph 结果中提取路径表名。"""
    table_names: list[str] = []
    for path in graph.paths:
        table_names.extend(path.tables)
    return table_names


def build_hybrid_retrieval_pipeline(
    connection_provider: Any,
    config: TableRAGConfig | None = None,
    database_type: str | None = None,
    index_store: IndexStoreSettings | None = None,
    retrieval_settings: RetrievalSettings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    rerank_provider: RerankProviderLike | None = None,
) -> HybridRetrievalPipeline:
    """按数据库类型装配混合检索 Pipeline。

    Args:
        connection_provider: 外部注入的索引库连接提供器。
        config: 可选 TableRAG 总配置；为空时使用默认配置。
        database_type: 可选数据库类型；为空时优先使用 config.database_type。
        index_store: 可选索引存储配置；传入时覆盖 config.index_store。
        retrieval_settings: 可选在线检索配置；传入时覆盖 config.retrieval。
        embedding_provider: 可选查询向量生成器，由外部实现并注入。
        rerank_provider: 可选外部重排序服务，由外部实现并注入。

    Returns:
        已按数据库类型装配好的混合检索 Pipeline。
    """
    config = config or TableRAGConfig()
    store_settings = index_store or config.index_store
    retrieval = retrieval_settings or config.retrieval
    normalized_database_type = normalize_database_type(database_type or config.database_type)
    if normalized_database_type == "postgresql":
        raw_hybrid_retriever = build_hybrid_retriever(
            connection_provider=connection_provider,
            config=config,
            database_type=normalized_database_type,
            index_store=store_settings,
            retrieval_settings=retrieval,
            embedding_provider=embedding_provider,
        )
        return HybridRetrievalPipeline(
            hybrid_retriever=raw_hybrid_retriever,
            reranker=build_ranking_pipeline(retrieval, rerank_provider),
            settings=retrieval,
        )
    raise UnsupportedRetrieverDatabaseError(
        f"Unsupported retriever database type: {database_type!r}. "
        f"Supported values: {', '.join(SUPPORTED_RETRIEVER_DATABASES)}"
    )


__all__ = ["HybridRetrievalPipeline", "build_hybrid_retrieval_pipeline"]
