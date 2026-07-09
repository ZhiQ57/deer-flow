"""字段值别名提供器定义和默认实现。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..schemas import FieldValueIndexTarget, TableValueIndexTarget


class AliasProvider(ABC):
    """字段值别名提供器抽象类，用于接入词库、知识图谱或人工映射表。"""

    @abstractmethod
    def aliases_for(self, target: TableValueIndexTarget, field: FieldValueIndexTarget, raw_value: str) -> list[str]:
        """查询一个字段值的别名。

        Args:
            target: 当前字段所属的逻辑表同步配置。
            field: 当前字段的同步配置。
            raw_value: 数据库中真实存储的字段值。

        Returns:
            与 raw_value 等价的同义词、简称、英文名或业务叫法列表。
        """


class EmptyAliasProvider(AliasProvider):
    """默认字段值别名提供器，不返回任何别名。"""

    def aliases_for(self, target: TableValueIndexTarget, field: FieldValueIndexTarget, raw_value: str) -> list[str]:
        """查询字段值别名。

        Args:
            target: 当前逻辑表配置。
            field: 当前字段配置。
            raw_value: 原始字段值。

        Returns:
            空列表；生产环境可用业务词库、知识图谱或人工映射表实现替换。
        """
        return []
