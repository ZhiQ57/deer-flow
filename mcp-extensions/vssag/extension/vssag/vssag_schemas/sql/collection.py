from __future__ import annotations

from pydantic import BaseModel, Field

from .collected_field_value import SQLCollectedFieldValue
from .evidence import SQLEvidence
from .field_index_statistics import SQLFieldIndexStatistics
from .graph_edge import SQLGraphEdge
from .table import SQLTableSchema


class SQLSchemaCollection(BaseModel):
    """Collected SQL schema records."""

    db_type: str
    database_id: int = 0
    collection_job_id: str = ""
    tables: list[SQLTableSchema] = Field(default_factory=list)
    field_statistics: list[SQLFieldIndexStatistics] = Field(default_factory=list)
    collected_field_values: list[SQLCollectedFieldValue] = Field(default_factory=list)
    evidence: list[SQLEvidence] = Field(default_factory=list)
    graph_edges: list[SQLGraphEdge] = Field(default_factory=list)
