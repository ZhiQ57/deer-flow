"""业务词库、同义词和指标别名提供器。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..utils.text import simple_text_normalize


@dataclass(frozen=True)
class GlossaryEntry:
    """业务词库条目。"""

    canonical: str
    aliases: tuple[str, ...] = ()
    category: str = "general"
    metadata: dict[str, str] = field(default_factory=dict)


class SynonymProvider(ABC):
    """同义词提供器抽象类，用于扩展用户查询中的业务说法。"""

    @abstractmethod
    def synonyms_for(self, term: str) -> list[str]:
        """查询一个词的同义表达。

        Args:
            term: 标准词或用户输入词。

        Returns:
            同义词、简称或业务别名列表。
        """


class BusinessGlossaryProvider(ABC):
    """业务词库提供器抽象类，用于识别指标、维度、实体和值类型词。"""

    @abstractmethod
    def entries(self) -> list[GlossaryEntry]:
        """返回业务词库条目。

        Args:
            无。

        Returns:
            词库条目列表。
        """


class MetricAliasProvider(ABC):
    """指标别名提供器抽象类，用于把 GMV、销售额等表达映射到标准指标。"""

    @abstractmethod
    def metric_aliases_for(self, term: str) -> list[str]:
        """查询指标别名。

        Args:
            term: 指标词或用户表达。

        Returns:
            指标标准名和别名列表。
        """


class InMemorySynonymProvider(SynonymProvider):
    """内存同义词提供器，适合默认词库、测试和轻量业务接入。"""

    def __init__(self, mapping: dict[str, list[str] | tuple[str, ...]] | None = None):
        """初始化同义词映射。

        Args:
            mapping: key 为词条，value 为同义词列表。

        Returns:
            None。
        """
        self._mapping: dict[str, list[str]] = {}
        for key, values in (mapping or {}).items():
            clean_key = simple_text_normalize(key)
            merged = [key, *values]
            for item in merged:
                normalized = simple_text_normalize(item)
                self._mapping.setdefault(normalized, [])
                self._mapping[normalized].extend(other for other in merged if other != item)

    def synonyms_for(self, term: str) -> list[str]:
        """查询一个词的同义表达。"""
        normalized = simple_text_normalize(term)
        return _dedupe(self._mapping.get(normalized, []))


class InMemoryBusinessGlossaryProvider(BusinessGlossaryProvider):
    """内存业务词库提供器。"""

    def __init__(self, entries: list[GlossaryEntry] | None = None):
        """初始化业务词库。

        Args:
            entries: 业务词库条目；为空时使用内置 Text2SQL 常用词库。

        Returns:
            None。
        """
        self._entries = entries or default_glossary_entries()

    def entries(self) -> list[GlossaryEntry]:
        """返回业务词库条目。"""
        return list(self._entries)


class InMemoryMetricAliasProvider(MetricAliasProvider):
    """内存指标别名提供器。"""

    def __init__(self, glossary: BusinessGlossaryProvider | None = None):
        """初始化指标别名提供器。

        Args:
            glossary: 可选业务词库；为空时使用默认词库。

        Returns:
            None。
        """
        self.glossary = glossary or InMemoryBusinessGlossaryProvider()

    def metric_aliases_for(self, term: str) -> list[str]:
        """查询指标别名。"""
        normalized = simple_text_normalize(term)
        for entry in self.glossary.entries():
            if entry.category != "metric":
                continue
            names = (entry.canonical, *entry.aliases)
            if any(simple_text_normalize(name) == normalized for name in names):
                return _dedupe(list(names))
        return []


def default_glossary_entries() -> list[GlossaryEntry]:
    """返回 SDK 内置的通用 Text2SQL 业务词库。

    Args:
        无。

    Returns:
        覆盖指标、维度、实体、状态和时间表达的词库条目。
    """
    return [
        GlossaryEntry("销售额", ("GMV", "成交额", "订单金额", "销售金额", "total amount"), "metric"),
        GlossaryEntry("销量", ("销售数量", "件数", "quantity"), "metric"),
        GlossaryEntry("实付金额", ("支付金额", "收款金额", "paid amount"), "metric"),
        GlossaryEntry("退款金额", ("退回金额", "refund amount"), "metric"),
        GlossaryEntry("库存数量", ("库存", "存量", "stock qty"), "metric"),
        GlossaryEntry("客户", ("会员", "买家", "企业客户", "customer"), "entity"),
        GlossaryEntry("商品", ("产品", "SKU", "货品", "product"), "entity"),
        GlossaryEntry("订单", ("销售订单", "交易单", "order"), "entity"),
        GlossaryEntry("门店", ("店铺", "网点", "store"), "entity"),
        GlossaryEntry("供应商", ("供货商", "supplier"), "entity"),
        GlossaryEntry("区域", ("地区", "大区", "region"), "dimension"),
        GlossaryEntry("渠道", ("来源", "销售渠道", "channel"), "dimension"),
        GlossaryEntry("品牌", ("厂牌", "brand"), "dimension"),
        GlossaryEntry("城市", ("城市场", "city"), "dimension"),
        GlossaryEntry("已完成", ("完成", "已签收", "履约完成"), "status"),
        GlossaryEntry("售后中", ("处理中", "待处理", "工单处理中"), "status"),
        GlossaryEntry("本月", ("当月", "这个月"), "time"),
        GlossaryEntry("上月", ("上个月", "上一个月"), "time"),
        GlossaryEntry("同比", ("去年同期", "year over year", "YoY"), "time"),
        GlossaryEntry("环比", ("较上期", "month over month", "MoM"), "time"),
    ]


def default_synonym_provider() -> InMemorySynonymProvider:
    """构造默认同义词提供器。"""
    return InMemorySynonymProvider({entry.canonical: list(entry.aliases) for entry in default_glossary_entries()})


def _dedupe(values: list[str]) -> list[str]:
    """按顺序去重并移除空字符串。"""
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "BusinessGlossaryProvider",
    "GlossaryEntry",
    "InMemoryBusinessGlossaryProvider",
    "InMemoryMetricAliasProvider",
    "InMemorySynonymProvider",
    "MetricAliasProvider",
    "SynonymProvider",
    "default_glossary_entries",
    "default_synonym_provider",
]
