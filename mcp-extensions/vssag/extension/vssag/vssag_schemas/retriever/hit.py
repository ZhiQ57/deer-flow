from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    record_id: str
    source_id: str
    score: float = 0.0
    text: str = ""
    heading_path: str = ""
    source_title: str = ""
    channels: list[str] = Field(default_factory=list)
    graph_score: float = 0.0
    related_to: list[str] = Field(default_factory=list)
    outgoing_links: list[str] = Field(default_factory=list)
    backlinks: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    citation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
