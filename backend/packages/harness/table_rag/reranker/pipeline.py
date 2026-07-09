"""粗排融合到模型精排的排序流水线。"""

from __future__ import annotations

from collections.abc import Sequence

from ..schemas import ColumnRetrievalResult, TableRetrievalResult, ValueRetrievalResult
from .base import AsyncRetrievalRerankerBase, RetrievalRerankerBase
from .fine_rankers import AsyncRetrievalFineRankerBase, RetrievalFineRankerBase
from .fusion import RetrievalFusionBase


class RankingPipeline(RetrievalRerankerBase, AsyncRetrievalRerankerBase):
    """检索排序流水线，按粗排融合再精排的顺序处理候选。"""

    def __init__(
        self,
        *,
        fusion: RetrievalFusionBase | None = None,
        fine_ranker: RetrievalFineRankerBase | None = None,
        async_fine_ranker: AsyncRetrievalFineRankerBase | None = None,
    ):
        """初始化排序流水线。

        Args:
            fusion: 可选多路召回融合器。
            fine_ranker: 可选同步精排器。
            async_fine_ranker: 可选异步精排器；为空时复用 fine_ranker。

        Returns:
            None。
        """
        self.fusion = fusion
        self.fine_ranker = fine_ranker
        self.async_fine_ranker = async_fine_ranker or (
            fine_ranker if isinstance(fine_ranker, AsyncRetrievalFineRankerBase) else None
        )

    def rerank_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """先融合粗排再同步精排候选表。

        Args:
            query: 用户问题。
            tables: 表候选。
            columns: 字段候选。
            values: 字段值候选。

        Returns:
            排序后的表候选。
        """
        fused_tables = self.fusion.fuse_tables(query, tables, columns, values) if self.fusion else list(tables)
        if self.fine_ranker is None:
            return fused_tables
        return self.fine_ranker.rerank_tables(query, fused_tables, columns, values)

    def rerank_columns(self, query: str, columns: Sequence[ColumnRetrievalResult]) -> list[ColumnRetrievalResult]:
        """先融合粗排再同步精排候选字段。

        Args:
            query: 用户问题。
            columns: 字段候选。

        Returns:
            排序后的字段候选。
        """
        fused_columns = self.fusion.fuse_columns(query, columns) if self.fusion else list(columns)
        if self.fine_ranker is None:
            return fused_columns
        return self.fine_ranker.rerank_columns(query, fused_columns)

    async def arerank_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """先融合粗排再异步精排候选表。

        Args:
            query: 用户问题。
            tables: 表候选。
            columns: 字段候选。
            values: 字段值候选。

        Returns:
            排序后的表候选。
        """
        fused_tables = self.fusion.fuse_tables(query, tables, columns, values) if self.fusion else list(tables)
        if self.async_fine_ranker is not None:
            return await self.async_fine_ranker.arerank_tables(query, fused_tables, columns, values)
        if self.fine_ranker is not None:
            return self.fine_ranker.rerank_tables(query, fused_tables, columns, values)
        return fused_tables

    async def arerank_columns(
        self,
        query: str,
        columns: Sequence[ColumnRetrievalResult],
    ) -> list[ColumnRetrievalResult]:
        """先融合粗排再异步精排候选字段。

        Args:
            query: 用户问题。
            columns: 字段候选。

        Returns:
            排序后的字段候选。
        """
        fused_columns = self.fusion.fuse_columns(query, columns) if self.fusion else list(columns)
        if self.async_fine_ranker is not None:
            return await self.async_fine_ranker.arerank_columns(query, fused_columns)
        if self.fine_ranker is not None:
            return self.fine_ranker.rerank_columns(query, fused_columns)
        return fused_columns


__all__ = ["RankingPipeline"]
