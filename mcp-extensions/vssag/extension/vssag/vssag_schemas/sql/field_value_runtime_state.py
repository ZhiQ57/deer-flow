from __future__ import annotations

from pydantic import BaseModel


class SQLFieldValueRuntimeState(BaseModel):
    """字段值索引记录的引用数量和生命周期状态。"""

    value_index_id: int
    value_count: int = 0
    status: str = "active"
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    tombstone_at: str | None = None
    collection_job_id: str = ""
