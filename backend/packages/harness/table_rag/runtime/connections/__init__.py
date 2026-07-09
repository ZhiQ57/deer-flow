"""TableRAG 运行时连接协议和校验模块。"""

from .base import ConnectionProvider
from .postgresqls import PostgresConnectionValidator, validate_postgres_connection
from .validators import (
    ConnectionValidationIssue,
    ConnectionValidationResult,
    ConnectionValidator,
    validate_connection,
)

__all__ = [
    "ConnectionProvider",
    "ConnectionValidationIssue",
    "ConnectionValidationResult",
    "ConnectionValidator",
    "PostgresConnectionValidator",
    "validate_connection",
    "validate_postgres_connection",
]