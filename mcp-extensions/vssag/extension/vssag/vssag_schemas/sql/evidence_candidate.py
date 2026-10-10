from __future__ import annotations

from pydantic import BaseModel


class SQLEvidenceCandidate(BaseModel):
    """Candidate SQL business evidence awaiting validation."""

    id: int | None = None
    source_query: str
    candidate_trigger: str
    candidate_evidence: str
    confidence: float = 0.0
    status: str = "pending"
    created_at: str | None = None
