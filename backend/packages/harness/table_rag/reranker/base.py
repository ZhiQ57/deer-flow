"""重排序器抽象定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ..schemas import ColumnRetrievalResult, TableRetrievalResult, ValueRetrievalResult


class RetrievalRerankerBase(ABC):
    """检索重排序抽象类，用于统一融合各路召回结果的分数。"""

    @abstractmethod
    def rerank_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """重排序候选表。

        Args:
            query: 用户问题。
            tables: 表召回结果。
            columns: 字段召回结果，用于给所属表补充分数。
            values: 字段值召回结果，用于给所属表补充分数。

        Returns:
            重排序后的表召回结果。
        """

    @abstractmethod
    def rerank_columns(self, query: str, columns: Sequence[ColumnRetrievalResult]) -> list[ColumnRetrievalResult]:
        """重排序候选字段。

        Args:
            query: 用户问题。
            columns: 字段召回结果。

        Returns:
            重排序后的字段召回结果。
        """


class AsyncRetrievalRerankerBase(ABC):
    """异步检索重排序抽象类，供异步多路召回链路统一调用。"""

    @abstractmethod
    async def arerank_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """异步重排序候选表。

        Args:
            query: 用户问题。
            tables: 表召回结果。
            columns: 字段召回结果，用于给所属表补充上下文。
            values: 字段值召回结果，用于给所属表补充上下文。

        Returns:
            重排序后的表召回结果。
        """

    @abstractmethod
    async def arerank_columns(
        self,
        query: str,
        columns: Sequence[ColumnRetrievalResult],
    ) -> list[ColumnRetrievalResult]:
        """异步重排序候选字段。

        Args:
            query: 用户问题。
            columns: 字段召回结果。

        Returns:
            重排序后的字段召回结果。
        """
