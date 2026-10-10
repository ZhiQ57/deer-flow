"""Generic index records.  This module intentionally knows nothing about Tiptap."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContentRecord(BaseModel):
    """One searchable unit supplied by any host application."""

    """One searchable unit supplied by any host application."""

    model_config = ConfigDict(extra="allow")

    record_id: str
    source_id: str
    text: str = ""
    title: str | None = None
    heading_path: str | None = None
    position: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


def __getattr__(name: str) -> Any:
    """Resolve the historical ``content.IndexPayload`` import lazily."""

    if name == "IndexPayload":
        from .payload import IndexPayload

        return IndexPayload
    raise AttributeError(name)


__all__ = ["ContentRecord", "IndexPayload"]
