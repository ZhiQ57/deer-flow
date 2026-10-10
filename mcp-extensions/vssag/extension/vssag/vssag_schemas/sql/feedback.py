from __future__ import annotations

from pydantic import BaseModel, Field


class SQLFeedback(BaseModel):
    """Feedback captured from a SQL retrieval interaction."""

    query: str
    generated_sql: str = ""
    execution_result: str = ""
    used_tables: list[str] = Field(default_factory=list)
    join_conditions: list[str] = Field(default_factory=list)
    confidence: float = 0.85
    evidence_content: str = ""
    candidate_trigger: str = ""
    candidate_evidence: str = ""
