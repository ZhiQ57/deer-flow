from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .column import SQLColumnInfo
from .foreign_key import SQLForeignKeyInfo
from .index import SQLIndexInfo


class SQLTableSchema(BaseModel):
    """Complete SQL table schema metadata."""

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    database_id: int = 0
    db_type: str
    raw_schema_name: str
    raw_table_name: str
    random_table_name: str
    raw_table_label: str = ""
    raw_ddl_text: str
    raw_ddl_hash: str
    random_ddl_text: str
    raw_schema_summary: str
    random_schema_summary: str
    columns: list[SQLColumnInfo] = Field(default_factory=list)
    foreign_keys: list[SQLForeignKeyInfo] = Field(default_factory=list)
    indexes: list[SQLIndexInfo] = Field(default_factory=list)
    row_count: int | None = None
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    qa_text: str = ""
    created_at: str | None = None
    updated_at: str | None = None
