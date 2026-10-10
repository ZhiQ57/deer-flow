from __future__ import annotations

from pydantic import BaseModel, Field

from .evidence import SQLEvidence
from .field_value_search_result import SQLFieldValueSearchResult
from .graph_edge import SQLGraphEdge
from .search_hit import SQLSearchHit
from .table import SQLTableSchema


class SQLSearchResponse(BaseModel):
    """SQL schema search response."""

    query: str
    hits: list[SQLSearchHit] = Field(default_factory=list)
    tables: list[SQLTableSchema] = Field(default_factory=list)
    values: list[SQLFieldValueSearchResult] = Field(default_factory=list)
    evidence: list[SQLEvidence] = Field(default_factory=list)
    graph_edges: list[SQLGraphEdge] = Field(default_factory=list)
