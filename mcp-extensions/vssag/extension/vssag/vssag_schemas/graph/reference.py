from __future__ import annotations

from pydantic import BaseModel, Field


class ReferenceRecord(BaseModel):
    edge_id: str
    source_id: str
    source_record_id: str | None = None
    target_key: str
    target_source_id: str | None = None
    target_record_id: str | None = None
    raw_target: str
    anchor_text: str = ""
    relation_type: str = "references"
    extraction_method: str = "explicit_markdown"
    confidence: float = 1.0
    status: str = "unresolved"
    source_position: int = 0
    extractor_version: str = "1"
