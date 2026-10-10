from __future__ import annotations

from pydantic import BaseModel, Field


class SQLFieldValueAnnotation(BaseModel):
    """字段值 LLM 标注记录。"""

    id: int | None = None
    value_index_id: int | None = None
    collection_job_id: str
    database_id: int
    schema_name: str
    raw_table_name: str
    column_name: str
    raw_value: str
    should_index: bool
    confidence: float = 0.0
    aliases: list[str] = Field(default_factory=list)
    reason: str = ""
    model_name: str
    prompt_version: str
    input_hash: str
    review_status: str = "pending"
    created_at: str | None = None
    updated_at: str | None = None
