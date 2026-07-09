"""混合检索 raw 召回器。"""

from __future__ import annotations

from collections.abc import Sequence

from ..schemas import (
    ColumnRetrievalResult,
    HybridRetrievalResult,
    RetrievalOptions,
    TableRetrievalResult,
    ValueRetrievalResult,
)
from .base import (
    ColumnRetrieverBase,
    EvidenceRetrieverBase,
    JoinGraphRetrieverBase,
    TableRetrieverBase,
    ValueRetrieverBase,
)


class HybridRetriever:
    """混合检索 raw 召回器，原样返回多路召回结果。

    作用：只调度 Evidence、表、列、字段值和 Join Graph 单路召回，不执行查询理解、
    候选融合重排序或 Join Graph 反补表候选。
    """

    def __init__(
        self,
        evidence_retriever: EvidenceRetrieverBase | None = None,
        table_retriever: TableRetrieverBase | None = None,
        column_retriever: ColumnRetrieverBase | None = None,
        value_retriever: ValueRetrieverBase | None = None,
        join_graph_retriever: JoinGraphRetrieverBase | None = None,
    ):
        """初始化 raw 混合检索器。

        Args:
            evidence_retriever: 可选 Evidence 检索器实例。
            table_retriever: 可选表检索器实例。
            column_retriever: 可选列检索器实例。
            value_retriever: 可选字段值检索器实例。
            join_graph_retriever: 可选 Join Graph 检索器实例。

        Returns:
            None。
        """
        self.evidence_retriever = evidence_retriever
        self.table_retriever = table_retriever
        self.column_retriever = column_retriever
        self.value_retriever = value_retriever
        self.join_graph_retriever = join_graph_retriever

    def retrieve(
        self,
        query: str,
        options: RetrievalOptions | None = None,
        schema_query: str | None = None,
    ) -> HybridRetrievalResult:
        """执行 raw 混合召回。

        Args:
            query: 原始用户问题，用于 Evidence 和字段值召回。
            options: 检索选项；为空时使用默认值。
            schema_query: 可选 schema 检索文本；传入时用于表和列召回。

        Returns:
            原样多路召回结果。
        """
        options = options or RetrievalOptions()
        schema_search_text = schema_query or query
        evidence_hits = self.evidence_retriever.search_evidences(query, options) if self.evidence_retriever else []
        table_hits = self.table_retriever.search_tables(schema_search_text, options) if self.table_retriever else []
        column_hits = self.column_retriever.search_columns(schema_search_text, options) if self.column_retriever else []
        value_hits = self.value_retriever.search_values(query, options) if self.value_retriever else []
        seed_tables = _collect_join_seed_tables(
            column_retriever=self.column_retriever,
            table_hits=table_hits,
            column_hits=column_hits,
            value_hits=value_hits,
        )
        join_graphs = (
            self.join_graph_retriever.expand_paths(seed_tables, options)
            if self.join_graph_retriever and seed_tables
            else []
        )

        return HybridRetrievalResult(
            query=query,
            tables=table_hits,
            columns=column_hits,
            values=value_hits,
            join_graphs=join_graphs,
            evidences=evidence_hits,
            metadata={
                "schema_query": schema_query,
                "evidence_recall_count": len(evidence_hits),
                "table_recall_count": len(table_hits),
                "column_recall_count": len(column_hits),
                "value_recall_count": len(value_hits),
                "join_seed_table_count": len(seed_tables),
                "join_graph_count": len(join_graphs),
                "mode": "raw_hybrid_retrieval",
            },
        )


def _collect_join_seed_tables(
    *,
    column_retriever: ColumnRetrieverBase | None,
    table_hits: Sequence[TableRetrievalResult],
    column_hits: Sequence[ColumnRetrievalResult],
    value_hits: Sequence[ValueRetrievalResult],
) -> list[str]:
    """从多路召回结果中收集 Join Graph 种子表。

    Args:
        column_retriever: 列召回器，用于字段反查表。
        table_hits: 表召回结果。
        column_hits: 列召回结果。
        value_hits: 字段值召回结果。

    Returns:
        按原始顺序去重后的 Join Graph 种子表。
    """
    table_names = [item.table_name for item in table_hits]
    table_names.extend(_table_names_from_column_mappings(column_retriever, column_hits))
    table_names.extend(item.table_name for item in value_hits)
    return _dedupe(table_names)


def _table_names_from_column_mappings(
    column_retriever: ColumnRetrieverBase | None,
    columns: Sequence[ColumnRetrievalResult],
) -> list[str]:
    """通过字段反向映射表补充表名。

    Args:
        column_retriever: 列字段召回器。
        columns: 字段召回结果。

    Returns:
        由字段反查得到的表名列表。
    """
    if not column_retriever:
        return []
    mappings = column_retriever.tables_for_columns([column.column_name for column in columns])
    return [mapping.table_name for mapping in mappings]


def _dedupe(values: Sequence[str]) -> list[str]:
    """按原始顺序去重。

    Args:
        values: 字符串列表。

    Returns:
        去重后的字符串列表。
    """
    return list(dict.fromkeys(value for value in values if value))


__all__ = ["HybridRetriever"]
