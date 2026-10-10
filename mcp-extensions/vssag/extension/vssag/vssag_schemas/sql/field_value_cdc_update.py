from __future__ import annotations

from pydantic import BaseModel

from .field_value_annotation import SQLFieldValueAnnotation


class SQLFieldValueCDCUpdate(BaseModel):
    """单个 CDC 事件对一个已索引字段值的计数变化。"""

    event_id: str
    collection_job_id: str
    database_id: int
    schema_name: str
    raw_table_name: str
    random_table_name: str
    column_name: str
    old_value: str | None = None
    new_value: str | None = None
    source_position: str
    source_order: int
    annotation: SQLFieldValueAnnotation | None = None
