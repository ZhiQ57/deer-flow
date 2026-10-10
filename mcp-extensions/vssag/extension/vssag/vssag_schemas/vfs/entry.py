from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class VFSEntry(BaseModel):
    name: str
    path: str
    type: Literal["file", "dir"] = "file"
    size: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
