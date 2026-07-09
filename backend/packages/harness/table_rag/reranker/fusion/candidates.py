"""多路召回候选合并工具。"""

from __future__ import annotations

from collections.abc import Sequence

from ...schemas import (
    ColumnRetrievalResult,
    JoinGraphRetrievalResult,
    TableRetrievalResult,
    ValueRetrievalResult,
)


def tables_from_columns(columns: Sequence[ColumnRetrievalResult]) -> list[TableRetrievalResult]:
    """由字段召回结果生成表候选补充项。

    Args:
        columns: 字段召回结果。

    Returns:
        根据字段所属表生成的表候选列表。
    """
    by_table: dict[str, float] = {}
    for column in columns:
        by_table[column.table_name] = max(by_table.get(column.table_name, 0.0), column.score)
    return [
        TableRetrievalResult(
            table_name=table_name,
            table_label=None,
            table_entities=[],
            table_describe="由字段召回反推",
            score=score,
            source_scores={"column_inferred": score},
        )
        for table_name, score in by_table.items()
    ]


def tables_from_values(values: Sequence[ValueRetrievalResult]) -> list[TableRetrievalResult]:
    """由字段值召回结果生成表候选补充项。

    Args:
        values: 字段值召回结果。

    Returns:
        根据字段值所属表生成的表候选列表。
    """
    by_table: dict[str, ValueRetrievalResult] = {}
    for value in values:
        current = by_table.get(value.table_name)
        if current is None or value.score > current.score:
            by_table[value.table_name] = value
    return [
        TableRetrievalResult(
            table_name=value.table_name,
            table_label=value.table_comment,
            table_entities=[],
            table_describe="由字段值召回反推",
            score=value.score,
            source_scores={"value_inferred": value.score},
        )
        for value in by_table.values()
    ]


def tables_from_join_graphs(graphs: Sequence[JoinGraphRetrievalResult]) -> list[TableRetrievalResult]:
    """由 Join Graph 路径生成表候选补充项。

    Args:
        graphs: Join Graph 召回结果。

    Returns:
        路径中所有表对应的候选表列表。
    """
    by_table: dict[str, float] = {}
    for graph in graphs:
        for path in graph.paths:
            for table_name in path.tables:
                by_table[table_name] = max(by_table.get(table_name, 0.0), path.score)
    return [
        TableRetrievalResult(
            table_name=table_name,
            table_label=None,
            table_entities=[],
            table_describe="由 Join Graph 路径补全",
            score=score,
            source_scores={"join_path": score},
        )
        for table_name, score in by_table.items()
    ]


def merge_table_hits(*groups: Sequence[TableRetrievalResult]) -> list[TableRetrievalResult]:
    """合并多路表候选。

    Args:
        groups: 多组表候选。

    Returns:
        按表名去重后的表候选列表。
    """
    merged: dict[str, TableRetrievalResult] = {}
    for group in groups:
        for table in group:
            current = merged.get(table.table_name)
            if current is None:
                merged[table.table_name] = table
                continue
            merged[table.table_name] = _merge_table_candidate(current, table)
    return list(merged.values())


def _merge_table_candidate(left: TableRetrievalResult, right: TableRetrievalResult) -> TableRetrievalResult:
    """合并同名表候选并保留更高主分。"""
    if right.score > left.score:
        winner, loser = right, left
    else:
        winner, loser = left, right
    source_scores = {**loser.source_scores, **winner.source_scores}
    metadata = {**loser.metadata, **winner.metadata}
    return TableRetrievalResult(
        table_name=winner.table_name,
        score=winner.score,
        table_label=winner.table_label or loser.table_label,
        table_entities=winner.table_entities or loser.table_entities,
        table_describe=winner.table_describe or loser.table_describe,
        source_scores=source_scores,
        metadata=metadata,
    )


__all__ = [
    "merge_table_hits",
    "tables_from_columns",
    "tables_from_join_graphs",
    "tables_from_values",
]
