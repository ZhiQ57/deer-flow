from __future__ import annotations

from pydantic import BaseModel, Field


class GraphContext(BaseModel):
    related_to: list[str] = Field(default_factory=list)
    outgoing_links: list[str] = Field(default_factory=list)
    backlinks: list[str] = Field(default_factory=list)
    link_count: int = 0
