"""数据库连接配置结构。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DatabaseConnectionSettings:
    """单个数据库连接配置，用于后端项目从配置文件读取 DSN。"""

    dsn: str | None = None
    connect_timeout: int = 10


@dataclass(frozen=True)
class DatabaseConfig:
    """TableRAG 后端数据库配置，区分索引库和业务源库。"""

    index_database: DatabaseConnectionSettings = DatabaseConnectionSettings()
    source_database: DatabaseConnectionSettings = DatabaseConnectionSettings()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatabaseConfig":
        """从配置字典构造数据库配置。

        Args:
            data: 外部 YAML 或 JSON 反序列化后的配置字典。

        Returns:
            数据库连接配置对象。
        """
        return cls(
            index_database=_connection_from_dict(data.get("index_database")),
            source_database=_connection_from_dict(data.get("source_database")),
        )

    def require_index_dsn(self) -> str:
        """读取索引库 DSN，缺失时抛出明确错误。

        Args:
            无。

        Returns:
            索引库 PostgreSQL DSN。
        """
        if not self.index_database.dsn:
            raise ValueError("Config must provide index_database.dsn or pass --dsn explicitly.")
        return self.index_database.dsn

    def source_dsn_or_index_dsn(self) -> str:
        """读取业务源库 DSN，缺失时回退到索引库 DSN。

        Args:
            无。

        Returns:
            业务源库 DSN 或索引库 DSN。
        """
        return self.source_database.dsn or self.require_index_dsn()


def _connection_from_dict(data: Any) -> DatabaseConnectionSettings:
    """把字典配置转换为单个数据库连接配置。

    Args:
        data: 数据库连接配置片段。

    Returns:
        标准数据库连接配置。
    """
    if data is None:
        return DatabaseConnectionSettings()
    if not isinstance(data, dict):
        raise ValueError("database config must be a mapping")
    return DatabaseConnectionSettings(
        dsn=data.get("dsn"),
        connect_timeout=int(data.get("connect_timeout", 10)),
    )
