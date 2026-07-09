"""运行时数据库连接提供器协议。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol


class ConnectionProvider(Protocol):
    """数据库连接提供器协议，由 SDK 外部负责实现和注入。"""

    def connect(self) -> AbstractContextManager[Any]:
        """返回一个数据库连接上下文。

        Args:
            无。

        Returns:
            支持 with 语句的数据库连接上下文。
        """
        ...