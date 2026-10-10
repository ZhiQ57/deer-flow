from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .content_item import ParsedContentItem
from .section import ParsedSection


class ParsedDocument(BaseModel):
    """One parsed document."""

    source_name: str
    parser: str
    markdown: str
    items: list[ParsedContentItem] = Field(default_factory=list)
    sections: list[ParsedSection] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
