from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GraphEdge(BaseModel):
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str = "references"
    confidence: float = 1.0
    evidence_record_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
