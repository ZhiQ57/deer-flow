"""字段值同步抽象定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from ....schemas import FieldValueIndexTarget, FieldValueRecord, TableValueIndexTarget


class ValueSourceReader(ABC):
    """业务源表字段值读取器抽象类，用于从源数据库抽取 distinct 字段值。"""

    @abstractmethod
    def iter_distinct_values(self, target: TableValueIndexTarget, field: FieldValueIndexTarget) -> Iterable[str]:
        """迭代读取一个配置字段的 distinct 非空取值。

        Args:
            target: 逻辑表同步配置，包含真实 schema/table 和过滤条件。
            field: 字段同步配置，包含逻辑字段名和真实字段名。

        Returns:
            字符串迭代器，逐个返回数据库中的真实字段值。
        """


class ValueIndexStore(ABC):
    """字段值索引存储抽象类，负责已存在索引表的数据写入和删除。"""

    @abstractmethod
    def upsert_values(self, records: Sequence[FieldValueRecord]) -> int:
        """批量写入或更新字段值索引记录。

        Args:
            records: 待入库的字段值索引记录列表。

        Returns:
            本次提交的记录数量。
        """

    @abstractmethod
    def delete_target(self, table_name: str, column_name: str | None = None) -> int:
        """删除某个逻辑表或某个字段的索引数据。

        Args:
            table_name: 逻辑表名。
            column_name: 可选字段名；为空时删除整张逻辑表的字段值索引。

        Returns:
            被删除的索引记录数量。
        """
