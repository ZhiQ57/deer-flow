from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .entry import VFSEntry


class VFSResponse(BaseModel):
    ok: bool = True
    mode: str = ""
    path: str = ""
    content: str = ""
    entries: list[VFSEntry] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    source_ids: list[str] = Field(default_factory=list)
