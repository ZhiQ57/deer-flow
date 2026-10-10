from __future__ import annotations

from pydantic import Field

from .content import ContentRecord


class IndexPayload(ContentRecord):
    """Enriched record written to an index store."""

    normalized_text: str = ""
    keywords: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    summary: str = ""
    embedding: list[float] | None = None
    embedding_model: str = ""
    embedding_dimension: int = 0
    embedding_version: str = "1"
    reference_ids: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    index_version: str = "1"
