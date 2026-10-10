from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SQLRelationType = Literal["physical", "candidate", "validated"]


class SQLGraphEdge(BaseModel):
    """SQL table relationship edge."""

    id: int | None = None
    db_type: str
    raw_schema_name: str
    related_raw_schema_name: str
    raw_table_name: str
    random_table_name: str
    raw_column_name: str | None = None
    random_column_name: str | None = None
    related_raw_table_name: str
    related_random_table_name: str
    related_raw_column_name: str | None = None
    related_random_column_name: str | None = None
    relation_type: SQLRelationType
    relation_direction: str = "bidirectional"
    cardinality: str = "unknown"
    join_condition: str = ""
    evidence_source: str
    evidence_content: str = ""
    confidence: float = 0.0
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None
