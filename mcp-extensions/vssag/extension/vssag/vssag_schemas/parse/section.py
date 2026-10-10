from __future__ import annotations

from pydantic import BaseModel, Field

from .content_item import ParsedContentItem


class ParsedSection(BaseModel):
    """One parsed document section."""

    title: str
    level: int = 1
    position: int = 0
    heading_path: str = ""
    items: list[ParsedContentItem] = Field(default_factory=list)
