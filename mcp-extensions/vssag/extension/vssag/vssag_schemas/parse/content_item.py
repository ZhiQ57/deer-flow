from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ParsedContentItem(BaseModel):
    """Normalized text or special content item produced by a parser."""

    item_id: str
    type: Literal["text", "heading", "paragraph", "image", "table", "equation", "code", "list", "other"] = "text"
    text: str = ""
    level: int = 0
    page_idx: int | None = None
    position: int = 0
    heading_path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
