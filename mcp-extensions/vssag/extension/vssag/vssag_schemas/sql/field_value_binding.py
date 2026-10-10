from __future__ import annotations

from pydantic import BaseModel


class SQLFieldValueBinding(BaseModel):
    """字段值检索结果对应的数据库表字段位置。"""

    value_index_id: int
    database_id: int
    schema_name: str
    raw_table_name: str
    random_table_name: str
    column_name: str
    raw_value: str
    value_count: int = 0
    score: float = 0.0
