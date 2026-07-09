"""多路召回融合抽象定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ...schemas import ColumnRetrievalResult, TableRetrievalResult, ValueRetrievalResult


class RetrievalFusionBase(ABC):
    """多路召回融合抽象类，用于候选级粗排和规则 boost。"""

    @abstractmethod
    def fuse_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """融合候选表。

        Args:
            query: 用户问题。
            tables: 表召回结果。
            columns: 字段召回结果。
            values: 字段值召回结果。

        Returns:
            粗排后的表候选。
        """

    @abstractmethod
    def fuse_columns(self, query: str, columns: Sequence[ColumnRetrievalResult]) -> list[ColumnRetrievalResult]:
        """融合候选字段。

        Args:
            query: 用户问题。
            columns: 字段召回结果。

        Returns:
            粗排后的字段候选。
        """


__all__ = ["RetrievalFusionBase"]
