"""VSSAG internal data contracts."""

from .index.content import ContentRecord
from .index.payload import IndexPayload
from .parse.document import ParsedDocument
from .parse.section import ParsedSection
from .parse.content_item import ParsedContentItem
from .retriever.request import SearchRequest
from .retriever.response import SearchResponse
from .retriever.hit import SearchHit
from .graph.reference import ReferenceRecord
from .graph.node import GraphNode
from .graph.edge import GraphEdge
from .graph.context import GraphContext
from .vfs.access_context import VSSAGAccessContext
from .vfs.entry import VFSEntry
from .vfs.request import VFSRequest
from .vfs.response import VFSResponse
from .sql import (
    SQLColumnInfo,
    SQLFeedback,
    SQLFieldValue,
    SQLFieldValueAnnotation,
    SQLFieldValueAnnotationDecision,
    SQLFieldValueCDCEvent,
    SQLFieldValueCDCUpdate,
    SQLFieldValueCollectionCheckpoint,
    SQLFieldValueCollectionJob,
    SQLFieldValueStreamMessage,
    SQLForeignKeyInfo,
    SQLGraphEdge,
    SQLSchemaCollection,
    SQLEvidenceCandidate,
    SQLSearchHit,
    SQLSearchResponse,
    SQLScope,
    SQLTableSchema,
    SQLToolRequest,
    SQLToolResponse,
)

__all__ = [
    "ContentRecord",
    "IndexPayload",
    "ParsedDocument",
    "ParsedSection",
    "ParsedContentItem",
    "SearchRequest",
    "SearchResponse",
    "SearchHit",
    "ReferenceRecord",
    "GraphNode",
    "GraphEdge",
    "GraphContext",
    "VSSAGAccessContext",
    "VFSEntry",
    "VFSRequest",
    "VFSResponse",
    "SQLColumnInfo",
    "SQLFeedback",
    "SQLFieldValue",
    "SQLFieldValueAnnotation",
    "SQLFieldValueAnnotationDecision",
    "SQLFieldValueCDCEvent",
    "SQLFieldValueCDCUpdate",
    "SQLFieldValueCollectionCheckpoint",
    "SQLFieldValueCollectionJob",
    "SQLFieldValueStreamMessage",
    "SQLForeignKeyInfo",
    "SQLGraphEdge",
    "SQLSchemaCollection",
    "SQLEvidenceCandidate",
    "SQLSearchHit",
    "SQLSearchResponse",
    "SQLScope",
    "SQLTableSchema",
    "SQLToolRequest",
    "SQLToolResponse",
]
