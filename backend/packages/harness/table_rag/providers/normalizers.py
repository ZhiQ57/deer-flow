"""文本归一化器定义和默认实现。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..utils.text import simple_text_normalize


class TextNormalizer(ABC):
    """文本归一化抽象类，用于把原始字段值转成更稳定的检索文本。"""

    @abstractmethod
    def normalize(self, value: str) -> str:
        """归一化单个文本。

        Args:
            value: 从数据库或用户问题中取得的原始文本。

        Returns:
            去空格、统一大小写、统一符号后的检索文本。
        """


class DefaultTextNormalizer(TextNormalizer):
    """默认文本归一化器，不依赖外部分词或 NLP 库。"""

    def normalize(self, value: str) -> str:
        """归一化字段值或用户查询文本。

        Args:
            value: 原始文本。

        Returns:
            统一全半角、大小写、空格和标点后的文本。
        """
        return simple_text_normalize(value)