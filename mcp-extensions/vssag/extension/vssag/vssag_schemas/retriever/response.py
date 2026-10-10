from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .hit import SearchHit


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    total: int = 0
    mode: str = "hybrid"
    citations: list[dict[str, Any]] = Field(default_factory=list)
    graph_nodes: list[dict[str, Any]] = Field(default_factory=list)
    graph_edges: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str | None = None
