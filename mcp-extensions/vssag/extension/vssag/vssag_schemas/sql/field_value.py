from __future__ import annotations

from pydantic import BaseModel, Field


class SQLFieldValue(BaseModel):
    """字段值索引记录。"""

    id: int | None = None
    database_id: int
    schema_name: str
    raw_value: str
    raw_table_name: str
    random_table_name: str
    column_name: str
    aliases: list[str] = Field(default_factory=list)
    retrieval_text: str = ""
    created_at: str | None = None
    updated_at: str | None = None
