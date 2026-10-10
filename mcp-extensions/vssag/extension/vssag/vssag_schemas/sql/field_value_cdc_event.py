from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SQLFieldValueCDCEvent(BaseModel):
    """PostgreSQL WAL 与 MySQL Binlog 的统一行变更事件。"""

    event_id: str = Field(min_length=1, max_length=128)
    database_id: int
    db_type: str
    operation: Literal[
        "insert",
        "update",
        "delete",
        "truncate",
        "schema",
    ]
    schema_name: str
    raw_table_name: str
    old_values: dict[str, Any] = Field(default_factory=dict)
    new_values: dict[str, Any] = Field(default_factory=dict)
    source_position: str = Field(min_length=1)
    source_order: int = Field(ge=0)
    transaction_id: str = ""
    occurred_at: str = ""
