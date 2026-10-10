from __future__ import annotations

from pydantic import BaseModel, Field


class SQLEvidence(BaseModel):
    """Validated SQL business evidence."""

    id: int | None = None
    triggers: list[str] = Field(default_factory=list)
    retrieval_text: str = ""
    evidence_content: str
    evidence_type: str = "rule"
    description: str = ""
    status: int = 1
    created_at: str | None = None
    updated_at: str | None = None
