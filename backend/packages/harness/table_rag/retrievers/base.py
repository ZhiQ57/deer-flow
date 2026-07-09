"""检索器模块统一抽象定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ..schemas import (
    ColumnRetrievalResult,
    ColumnTableMapping,
    EvidenceRetrievalResult,
    JoinGraphRetrievalResult,
    RetrievalOptions,
    TableRetrievalResult,
    ValueRetrievalResult,
)


class EvidenceRetrieverBase(ABC):
    """业务规则和证据召回抽象类，用于召回 SQL 生成需要遵守的口径、术语和规则。"""

    @abstractmethod
    def search_evidences(self, query: str, options: RetrievalOptions) -> list[EvidenceRetrievalResult]:
        """召回业务规则和证据。

        Args:
            query: 用户问题或检索片段。
            options: 检索数量和过滤参数。

        Returns:
            Evidence 召回结果列表。
        """

    def search_evidences_keylist(
        self,
        keywords: Sequence[str],
        options: RetrievalOptions,
    ) -> list[EvidenceRetrievalResult]:
        """按关键词列表召回 Evidence。

        Args:
            keywords: 已由业务侧或 query_parser 抽取好的关键词列表。
            options: 检索数量和过滤参数。

        Returns:
            融合后的 Evidence 召回结果列表。
        """
        raise NotImplementedError("当前 Evidence 检索器未实现 search_evidences_keylist 能力")


class TableRetrieverBase(ABC):
    """表结构召回抽象类，用于召回与用户问题相关的候选表。"""

    @abstractmethod
    def search_tables(self, query: str, options: RetrievalOptions) -> list[TableRetrievalResult]:
        """召回候选表。

        Args:
            query: 用户问题或检索片段。
            options: 检索数量和过滤参数。

        Returns:
            表召回结果列表。
        """

    def search_tables_keylist(
        self,
        keywords: Sequence[str],
        options: RetrievalOptions,
    ) -> list[TableRetrievalResult]:
        """按关键词列表召回候选表。

        Args:
            keywords: 已由业务侧或 query_parser 抽取好的关键词列表。
            options: 检索数量和过滤参数。

        Returns:
            融合后的表召回结果列表。
        """
        raise NotImplementedError("当前表检索器未实现 search_tables_keylist 能力")


class ColumnRetrieverBase(ABC):
    """列字段召回抽象类，用于召回与用户问题相关的候选字段。"""

    @abstractmethod
    def search_columns(self, query: str, options: RetrievalOptions) -> list[ColumnRetrievalResult]:
        """召回候选字段。

        Args:
            query: 用户问题或检索片段。
            options: 检索数量和过滤参数。

        Returns:
            字段召回结果列表。
        """

    def search_columns_keylist(
        self,
        keywords: Sequence[str],
        options: RetrievalOptions,
    ) -> list[ColumnRetrievalResult]:
        """按关键词列表召回候选字段。

        Args:
            keywords: 已由业务侧或 query_parser 抽取好的关键词列表。
            options: 检索数量和过滤参数。

        Returns:
            融合后的字段召回结果列表。
        """
        raise NotImplementedError("当前字段检索器未实现 search_columns_keylist 能力")

    def tables_for_columns(self, column_names: Sequence[str]) -> list[ColumnTableMapping]:
        """根据字段名反查所属表。

        Args:
            column_names: 字段名列表。

        Returns:
            字段到表的反向映射列表；默认实现返回空列表。
        """
        return []


class ValueRetrieverBase(ABC):
    """字段值召回抽象类，用于召回与用户问题相关的候选字段值。"""

    @abstractmethod
    def search_values(self, query: str, options: RetrievalOptions) -> list[ValueRetrievalResult]:
        """召回候选字段值。

        Args:
            query: 用户问题或检索片段。
            options: 检索数量和过滤参数。

        Returns:
            字段值召回结果列表。
        """

    def search_values_keylist(
        self,
        keywords: Sequence[str],
        options: RetrievalOptions,
    ) -> list[ValueRetrievalResult]:
        """按关键词列表召回候选字段值。

        Args:
            keywords: 已由业务侧或 query_parser 抽取好的关键词列表。
            options: 检索数量和过滤参数。

        Returns:
            融合后的字段值召回结果列表。
        """
        raise NotImplementedError("当前字段值检索器未实现 search_values_keylist 能力")


class JoinGraphRetrieverBase(ABC):
    """Schema Join Graph 召回抽象类，用于补全候选表之间的连接路径。"""

    @abstractmethod
    def expand_paths(self, table_names: Sequence[str], options: RetrievalOptions) -> list[JoinGraphRetrievalResult]:
        """扩展候选表之间的 JOIN 路径。

        Args:
            table_names: 候选表名列表。
            options: 包含最大跳数等图谱检索参数。

        Returns:
            Join Graph 召回结果列表。
        """
