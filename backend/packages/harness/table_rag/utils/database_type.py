"""检索器数据库类型路由工具。"""

from __future__ import annotations

SUPPORTED_RETRIEVER_DATABASES = ("postgresql",)
_POSTGRES_ALIASES = {"postgresql", "postgres", "pg", "postgresqls"}


class UnsupportedRetrieverDatabaseError(ValueError):
    """不支持的检索数据库类型错误。"""


def normalize_database_type(database_type: str) -> str:
    """归一化检索数据库类型。

    Args:
        database_type: 外部传入的数据库类型名称。

    Returns:
        SDK 内部使用的标准数据库类型。
    """
    normalized = database_type.strip().lower().replace("-", "_")
    if normalized in _POSTGRES_ALIASES:
        return "postgresql"
    raise UnsupportedRetrieverDatabaseError(
        f"Unsupported retriever database type: {database_type!r}. "
        f"Supported values: {', '.join(SUPPORTED_RETRIEVER_DATABASES)}"
    )
