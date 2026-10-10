from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .evidence import SQLEvidence
from .field_value_search_result import SQLFieldValueSearchResult
from .graph_edge import SQLGraphEdge
from .table import SQLTableSchema


class SQLSearchHit(BaseModel):
    """One typed SQL schema search hit."""

    kind: Literal["table", "value", "evidence", "graph"]
    score: float
    table: SQLTableSchema | None = None
    value: SQLFieldValueSearchResult | None = None
    evidence: SQLEvidence | None = None
    edge: SQLGraphEdge | None = None
