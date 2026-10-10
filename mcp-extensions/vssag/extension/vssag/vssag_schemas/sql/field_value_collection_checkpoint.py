from __future__ import annotations

from pydantic import BaseModel


class SQLFieldValueCollectionCheckpoint(BaseModel):
    """字段值采集任务的可恢复位置。"""

    collection_job_id: str
    schema_name: str
    raw_table_name: str
    column_name: str
    last_raw_value: str = ""
    processed_values: int = 0
    status: str = "running"
