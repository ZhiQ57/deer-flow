from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..vfs.access_context import VSSAGAccessContext


class SearchRequest(BaseModel):
    query: str
    context: VSSAGAccessContext | None = None
    source_ids: list[str] | None = None
    strategy: Literal["keyword", "vector", "hybrid", "graph"] = "hybrid"
    top_k: int = Field(default=10, ge=1, le=200)
    include_graph: bool = True
    include_content: bool = True
