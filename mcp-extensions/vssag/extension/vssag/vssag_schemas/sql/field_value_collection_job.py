from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SQLFieldValueCollectionJob(BaseModel):
    """一次字段值采集任务的运行状态。"""

    job_id: str
    database_id: int
    db_type: str
    mode: str
    status: str = "running"
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    finished_at: datetime | None = None
    processed_tables: int = 0
    processed_fields: int = 0
    processed_values: int = 0
    error_count: int = 0
    error_message: str = ""
    cdc_start_position: str = ""
    cdc_end_position: str = ""
