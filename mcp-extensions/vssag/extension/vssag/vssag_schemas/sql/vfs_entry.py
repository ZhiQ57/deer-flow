from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SQLVFSEntry(BaseModel):
    """Entry in the SQL virtual directory."""

    name: str
    path: str
    type: Literal["file", "dir"]
    size: int = 0
