from __future__ import annotations

from pydantic import BaseModel, Field


class SQLIndexInfo(BaseModel):
    """SQL index metadata."""

    name: str
    columns: list[str] = Field(default_factory=list)
    unique: bool = False
    index_type: str = ""
