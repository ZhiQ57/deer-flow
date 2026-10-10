"""Virtual System Search Augment Generate SDK."""

from typing import TYPE_CHECKING, Any

from .config import (
    SQLAbilityConfig,
    SQLFieldValueCDCConfig,
    SQLFieldValueLLMAnnotationConfig,
    SQLKafkaStreamConfig,
    SQLMySQLBinlogConfig,
    SQLPostgresWALConfig,
    SQLRedisStreamConfig,
    SQLFieldValueIndexConfig,
    SQLGraphConfig,
    SQLIndexConfig,
    SQLEmbeddingConfig,
    SQLQAConfig,
    SQLRetrieverServiceConfig,
    SQLSecurityConfig,
    SQLTargetSourceConfig,
    VSSAGConfig,
)
from .vssag_schemas import (
    ContentRecord,
    IndexPayload,
    ParsedContentItem,
    ParsedDocument,
    ParsedSection,
    SearchHit,
    SearchRequest,
    SearchResponse,
    VSSAGAccessContext,
    VFSEntry,
    VFSRequest,
    VFSResponse,
    GraphNode,
    GraphEdge,
    GraphContext,
    ReferenceRecord,
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

if TYPE_CHECKING:
    from .client import VSSAGClient, parse_document


def __getattr__(name: str) -> Any:
    """按需加载会初始化解析、索引和 VFS 依赖的公开入口。

    Args:
        name: 请求访问的模块属性名。

    Returns:
        延迟加载后的 Client 类型或文档解析函数。

    Raises:
        AttributeError: 请求的属性不是延迟导出对象。
    """

    if name in {"VSSAGClient", "parse_document"}:
        from .client import VSSAGClient, parse_document

        globals()["VSSAGClient"] = VSSAGClient
        globals()["parse_document"] = parse_document
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "VSSAGClient",
    "VSSAGConfig",
    "SQLAbilityConfig",
    "SQLFieldValueCDCConfig",
    "SQLFieldValueLLMAnnotationConfig",
    "SQLKafkaStreamConfig",
    "SQLMySQLBinlogConfig",
    "SQLPostgresWALConfig",
    "SQLRedisStreamConfig",
    "SQLFieldValueIndexConfig",
    "SQLGraphConfig",
    "SQLIndexConfig",
    "SQLEmbeddingConfig",
    "SQLQAConfig",
    "SQLRetrieverServiceConfig",
    "SQLSecurityConfig",
    "SQLTargetSourceConfig",
    "ContentRecord",
    "IndexPayload",
    "ParsedContentItem",
    "ParsedDocument",
    "ParsedSection",
    "SearchHit",
    "SearchRequest",
    "SearchResponse",
    "VSSAGAccessContext",
    "VFSEntry",
    "VFSRequest",
    "VFSResponse",
    "GraphNode",
    "GraphEdge",
    "GraphContext",
    "ReferenceRecord",
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
    "parse_document",
]
