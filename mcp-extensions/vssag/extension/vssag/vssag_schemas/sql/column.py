from __future__ import annotations

from pydantic import BaseModel, Field


class SQLColumnInfo(BaseModel):
    """SQL column metadata."""

    name: str
    random_name: str
    data_type: str = ""
    nullable: bool = True
    default: str | None = None
    primary_key: bool = False
    ordinal_position: int = 0
    comment: str = ""
    enum_values: list[str] = Field(default_factory=list)
