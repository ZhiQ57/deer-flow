"""RRF 多路召回融合实现。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ...schemas import ColumnRetrievalResult, TableRetrievalResult, ValueRetrievalResult
from .base import RetrievalFusionBase
from .candidates import merge_table_hits, tables_from_columns, tables_from_values


class ReciprocalRankFusion(RetrievalFusionBase):
    """Reciprocal Rank Fusion 粗排融合器，适合多路召回分数不可比的场景。"""

    def __init__(self, k: int = 60):
        """初始化 RRF 融合器。

        Args:
            k: 排名平滑常数，常见取值为 30、60 或 100。

        Returns:
            None。
        """
        if k <= 0:
            raise ValueError("rrf k must be a positive integer")
        self.k = k

    def fuse_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """用 RRF 融合表、字段反推表和字段值反推表候选。

        Args:
            query: 用户问题。
            tables: 表召回结果。
            columns: 字段召回结果。
            values: 字段值召回结果。

        Returns:
            RRF 粗排后的表候选。
        """
        column_tables = tables_from_columns(columns)
        value_tables = tables_from_values(values)
        candidates = merge_table_hits(tables, column_tables, value_tables)
        if not candidates:
            return []
        rank_groups = [
            [table.table_name for table in tables],
            [table.table_name for table in column_tables],
            [table.table_name for table in value_tables],
        ]
        rrf_scores = reciprocal_rank_fusion_scores(rank_groups, k=self.k)
        fused = []
        for table in candidates:
            score = rrf_scores.get(table.table_name, 0.0)
            source_scores = {
                **table.source_scores,
                "pre_fusion": table.score,
                "rrf": score,
                "fusion": score,
                "rerank": score,
            }
            fused.append(replace(table, score=score, source_scores=source_scores))
        return sorted(fused, key=lambda item: item.score, reverse=True)

    def fuse_columns(self, query: str, columns: Sequence[ColumnRetrievalResult]) -> list[ColumnRetrievalResult]:
        """用 RRF 融合字段候选。

        Args:
            query: 用户问题。
            columns: 字段召回结果。

        Returns:
            RRF 粗排后的字段候选。
        """
        rank_group = [f"{column.table_name}.{column.column_name}" for column in columns]
        rrf_scores = reciprocal_rank_fusion_scores([rank_group], k=self.k)
        fused = []
        for column in columns:
            identifier = f"{column.table_name}.{column.column_name}"
            score = rrf_scores.get(identifier, 0.0)
            source_scores = {
                **column.source_scores,
                "pre_fusion": column.score,
                "rrf": score,
                "fusion": score,
                "rerank": score,
            }
            fused.append(replace(column, score=score, source_scores=source_scores))
        return sorted(fused, key=lambda item: item.score, reverse=True)

def reciprocal_rank_fusion_scores(rank_groups: Sequence[Sequence[str]], *, k: int = 60) -> dict[str, float]:
    """根据多组候选排名计算 RRF 分数。

    Args:
        rank_groups: 多路召回的候选标识列表，列表顺序代表排名。
        k: 排名平滑常数。

    Returns:
        候选标识到 RRF 分数的映射。
    """
    if k <= 0:
        raise ValueError("rrf k must be a positive integer")
    scores: dict[str, float] = {}
    for group in rank_groups:
        seen: set[str] = set()
        for rank, identifier in enumerate(group, start=1):
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (k + rank)
    return scores


__all__ = ["ReciprocalRankFusion", "reciprocal_rank_fusion_scores"]
