from __future__ import annotations

from pydantic import BaseModel, Field

from .field_value_binding import SQLFieldValueBinding


class SQLFieldValueSearchResult(BaseModel):
    """按自然语言等价值聚合后的字段值检索结果。"""

    raw_value: str
    aliases: list[str] = Field(default_factory=list)
    retrieval_text: str = ""
    bindings: list[SQLFieldValueBinding] = Field(default_factory=list)
    total_value_count: int = 0
