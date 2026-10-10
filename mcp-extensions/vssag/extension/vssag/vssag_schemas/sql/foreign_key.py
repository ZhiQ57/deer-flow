from __future__ import annotations

from pydantic import BaseModel


class SQLForeignKeyInfo(BaseModel):
    """SQL foreign-key metadata."""

    column_name: str
    referred_schema: str
    referred_table: str
    referred_column: str
    constraint_name: str | None = None
    cardinality: str = "unknown"
