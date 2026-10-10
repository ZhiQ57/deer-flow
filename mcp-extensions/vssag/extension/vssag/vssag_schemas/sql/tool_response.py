from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .vfs_entry import SQLVFSEntry


class SQLToolResponse(BaseModel):
    """Result of a SQL virtual-directory operation."""

    ok: bool = True
    mode: str = ""
    path: str = ""
    content: str = ""
    entries: list[SQLVFSEntry] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    table_names: list[str] = Field(default_factory=list)
