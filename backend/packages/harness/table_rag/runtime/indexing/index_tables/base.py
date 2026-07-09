"""通用索引初始化接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ....configs import TableRAGConfig


@dataclass(frozen=True)
class IndexInitializationResult:
    """索引初始化结果，记录执行语句数量和附加元数据。"""

    applied_statements: int = 0
    created_extensions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class IndexInitializer(ABC):
    """索引初始化器抽象类，负责显式触发索引生命周期初始化。"""

    @abstractmethod
    def initialize(self, config: TableRAGConfig) -> IndexInitializationResult:
        """根据配置初始化索引结构。

        Args:
            config: TableRAG 总配置。

        Returns:
            索引初始化结果。
        """
