from __future__ import annotations

from pydantic import BaseModel, Field


class SQLFieldIndexStatistics(BaseModel):
    """字段值索引判断所需的字段统计信息。"""

    collection_job_id: str
    database_id: int
    db_type: str
    schema_name: str
    raw_table_name: str
    column_name: str
    data_type: str = ""
    comment: str = ""
    row_count: int | None = None
    non_null_count: int = 0
    distinct_count: int = 0
    distinct_ratio: float = 0.0
    null_ratio: float = 0.0
    avg_length: float = 0.0
    max_length: int = 0
    pattern_distribution: dict[str, float] = Field(default_factory=dict)
    type_score: float = 0.0
    semantic_name_score: float = 0.0
    cardinality_score: float = 0.0
    quality_score: float = 0.0
    usage_score: float = 0.0
    field_score: float = 0.0
    selection_decision: str = "rejected"
    rejection_reason: str = ""
    values_truncated: bool = False
