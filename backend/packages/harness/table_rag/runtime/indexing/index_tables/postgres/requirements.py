"""PostgreSQL 索引运行要求定义和检测辅助。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .....configs import IndexStoreSettings, TableRAGConfig


@dataclass(frozen=True)
class PostgresIndexTableRequirement:
    """PostgreSQL 索引表结构要求。"""

    schema_name: str
    table_name: str
    required_columns: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class PostgresRuntimeRequirements:
    """PostgreSQL 运行时要求，包含版本、扩展和索引表结构。"""

    min_version_num: int = 120000
    required_extensions: set[str] = field(default_factory=set)
    required_tables: list[PostgresIndexTableRequirement] = field(default_factory=list)
    required_indexes: dict[str, set[str]] = field(default_factory=dict)
    required_schema_versions: dict[str, int] = field(default_factory=dict)


INDEX_SCHEMA_VERSION = 6
INDEX_METADATA_TABLE_NAME = "agent_index_metadata"

INDEX_COMPONENT_SCHEMA_VERSIONS = {
    "evidence_index": INDEX_SCHEMA_VERSION,
    "table_index": INDEX_SCHEMA_VERSION,
    "column_index": INDEX_SCHEMA_VERSION,
    "join_graph": INDEX_SCHEMA_VERSION,
    "field_value_index": INDEX_SCHEMA_VERSION,
}

INDEX_METADATA_COLUMNS = {
    "component",
    "schema_version",
    "metadata",
    "updated_at",
}


EVIDENCE_INDEX_COLUMNS = {
    "id",
    "triggers",
    "retrieval_text",
    "evidence_content",
    "evidence_type",
    "description",
    "status",
    "created_at",
    "updated_at",
}


TABLE_INDEX_COLUMNS = {
    "id",
    "db_type",
    "table_name",
    "table_label",
    "ddl_text",
    "ddl_hash",
    "schema_summary",
    "created_at",
    "updated_at",
}

COLUMN_INDEX_COLUMNS = {
    "id",
    "table_name",
    "column_name",
    "column_comment",
    "column_entities",
    "retrieval_text",
    "created_at",
    "updated_at",
}

JOIN_EDGE_COLUMNS = {
    "id",
    "source_table",
    "target_table",
    "join_condition",
    "edge_type",
    "weight",
    "created_at",
    "updated_at",
}

VALUE_INDEX_COLUMNS = {
    "id",
    "raw_value",
    "table_name",
    "column_name",
    "aliases",
    "retrieval_text",
    "created_at",
    "updated_at",
}

EMBEDDING_INDEX_COLUMNS = {
    "id",
    "model_name",
    "embedding",
    "created_at",
}


def required_extensions_for_config(config: TableRAGConfig) -> set[str]:
    """根据配置计算 PostgreSQL 必需扩展。

    Args:
        config: TableRAG 总配置。

    Returns:
        必需扩展名集合。
    """
    extensions: set[str] = set()
    if config.index_store.enable_pg_trgm:
        extensions.add("pg_trgm")
    if config.index_store.enable_bm25:
        extensions.add("pg_search")
    if config.index_store.requires_pgvector:
        extensions.add("vector")
    return extensions


def required_tables_for_config(config: TableRAGConfig) -> list[PostgresIndexTableRequirement]:
    """根据配置计算 PostgreSQL 在线检索必需索引表结构要求。

    Args:
        config: TableRAG 总配置。

    Returns:
        在线检索必需索引表结构要求列表。
    """
    settings = config.index_store
    requirements = [
        *schema_index_table_requirements(settings),
        field_value_index_table_requirement(settings),
    ]
    if settings.evidence_embedding_enabled or settings.field_value_embedding_enabled:
        requirements.extend(embedding_index_table_requirements(settings))
    return requirements


def required_indexes_for_config(config: TableRAGConfig) -> dict[str, set[str]]:
    """根据配置计算 PostgreSQL 必需索引名。

    Args:
        config: TableRAG 总配置。

    Returns:
        必需索引名集合。
    """
    settings = config.index_store
    required: set[str] = set()
    if settings.enable_bm25:
        required.update(
            {
                f"{settings.evidence_index_name}_bm25_idx",
                f"{settings.table_index_name}_bm25_idx",
                f"{settings.column_index_name}_bm25_idx",
                f"{settings.field_value_table_name}_bm25_idx",
            }
        )
    if settings.enable_pg_trgm:
        required.update(
            {
                f"{settings.evidence_index_name}_retrieval_text_trgm_idx",
                f"{settings.table_index_name}_schema_summary_trgm_idx",
                f"{settings.column_index_name}_retrieval_text_trgm_idx",
                f"{settings.field_value_table_name}_retrieval_text_trgm_idx",
            }
        )
    required.update(
        {
            f"{settings.join_edge_table_name}_source_idx",
            f"{settings.join_edge_table_name}_target_idx",
            f"{settings.join_edge_table_name}_source_target_idx",
            f"{settings.join_edge_table_name}_weight_idx",
        }
    )
    if settings.column_embedding_enabled:
        required.add(f"{settings.column_index_name}_embedding_hnsw_idx")
    if settings.evidence_embedding_enabled:
        required.update(
            {
                f"{settings.evidence_embedding_table_name}_hnsw_cosine_idx",
                f"{settings.evidence_embedding_table_name}_hnsw_ip_idx",
                f"{settings.evidence_embedding_table_name}_hnsw_l2_idx",
            }
        )
    if settings.field_value_embedding_enabled:
        required.add(f"{settings.field_value_embedding_table_name}_hnsw_cosine_idx")
    if not required:
        return {}
    return {
        settings.schema_name: required
    }


def index_metadata_table_requirement(settings: IndexStoreSettings) -> PostgresIndexTableRequirement:
    """生成 SDK 托管索引元数据表结构要求。

    Args:
        settings: 索引存储配置。

    Returns:
        索引元数据表结构要求。
    """
    return PostgresIndexTableRequirement(settings.schema_name, INDEX_METADATA_TABLE_NAME, set(INDEX_METADATA_COLUMNS))


def required_schema_versions_for_config(config: TableRAGConfig) -> dict[str, int]:
    """根据配置计算索引组件 schema version 要求。

    Args:
        config: TableRAG 总配置。

    Returns:
        组件名到最低 schema version 的映射。
    """
    return dict(INDEX_COMPONENT_SCHEMA_VERSIONS)


def schema_index_table_requirements(settings: IndexStoreSettings) -> list[PostgresIndexTableRequirement]:
    """生成表、列和 Join Graph 索引表结构要求。

    Args:
        settings: 索引存储配置。

    Returns:
        表、列和 Join Graph 索引表要求。
    """
    table_columns = set(TABLE_INDEX_COLUMNS)
    if settings.table_embedding_enabled:
        table_columns.add("embedding")
    column_columns = set(COLUMN_INDEX_COLUMNS)
    if settings.column_embedding_enabled:
        column_columns.add("embedding")
    return [
        PostgresIndexTableRequirement(settings.schema_name, settings.evidence_index_name, set(EVIDENCE_INDEX_COLUMNS)),
        PostgresIndexTableRequirement(settings.schema_name, settings.table_index_name, table_columns),
        PostgresIndexTableRequirement(settings.schema_name, settings.column_index_name, column_columns),
        PostgresIndexTableRequirement(settings.schema_name, settings.join_edge_table_name, set(JOIN_EDGE_COLUMNS)),
    ]


def field_value_index_table_requirement(settings: IndexStoreSettings) -> PostgresIndexTableRequirement:
    """生成字段值索引表结构要求。

    Args:
        settings: 索引存储配置。

    Returns:
        字段值索引表要求。
    """
    return PostgresIndexTableRequirement(settings.schema_name, settings.field_value_table_name, set(VALUE_INDEX_COLUMNS))


def embedding_index_table_requirements(settings: IndexStoreSettings) -> list[PostgresIndexTableRequirement]:
    """生成可选向量索引表结构要求。"""
    requirements: list[PostgresIndexTableRequirement] = []
    if settings.evidence_embedding_enabled:
        evidence_columns = set(EMBEDDING_INDEX_COLUMNS)
        evidence_columns.add("evidence_index_id")
        requirements.append(
            PostgresIndexTableRequirement(settings.schema_name, settings.evidence_embedding_table_name, evidence_columns)
        )
    if settings.field_value_embedding_enabled:
        value_columns = set(EMBEDDING_INDEX_COLUMNS)
        value_columns.add("value_index_id")
        requirements.append(
            PostgresIndexTableRequirement(settings.schema_name, settings.field_value_embedding_table_name, value_columns)
        )
    return requirements


def build_postgres_runtime_requirements(config: TableRAGConfig) -> PostgresRuntimeRequirements:
    """根据配置构造 PostgreSQL 运行时要求。

    Args:
        config: TableRAG 总配置。

    Returns:
        PostgreSQL 运行时要求对象。
    """
    return PostgresRuntimeRequirements(
        required_extensions=required_extensions_for_config(config),
        required_tables=required_tables_for_config(config),
        required_indexes=required_indexes_for_config(config),
        required_schema_versions=required_schema_versions_for_config(config),
    )
