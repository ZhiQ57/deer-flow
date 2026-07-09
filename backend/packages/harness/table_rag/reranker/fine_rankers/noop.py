"""透传精排器实现。"""

from __future__ import annotations

from collections.abc import Sequence

from ...schemas import ColumnRetrievalResult, TableRetrievalResult, ValueRetrievalResult
from .base import RetrievalFineRankerBase


class NoopFineRanker(RetrievalFineRankerBase):
    """禁用精排时使用的透传精排器。"""

    def rerank_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """保持候选表原始顺序。

        Args:
            query: 用户问题。
            tables: 表候选。
            columns: 字段候选。
            values: 字段值候选。

        Returns:
            原始表候选列表。
        """
        return list(tables)

    def rerank_columns(self, query: str, columns: Sequence[ColumnRetrievalResult]) -> list[ColumnRetrievalResult]:
        """保持候选字段原始顺序。

        Args:
            query: 用户问题。
            columns: 字段候选。

        Returns:
            原始字段候选列表。
        """
        return list(columns)

__all__ = ["NoopFineRanker"]
