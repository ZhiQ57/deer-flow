from __future__ import annotations

from pydantic import BaseModel

from .field_value import SQLFieldValue
from .field_value_annotation import SQLFieldValueAnnotation


class SQLCollectedFieldValue(BaseModel):
    """一次采集得到的字段值及其数据库引用数量。"""

    value: SQLFieldValue
    value_count: int = 0
    annotation: SQLFieldValueAnnotation | None = None
