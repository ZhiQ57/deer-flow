"""PostgreSQL TableRAG 索引表 DDL 定义。"""

from __future__ import annotations

from .....configs import IndexStoreSettings, TableRAGConfig
from .....utils.serialization import json_dumps
from .....utils.validation import validate_safe_identifier
from .requirements import (
    INDEX_METADATA_TABLE_NAME,
    required_extensions_for_config,
    required_schema_versions_for_config,
)


def quote_identifier(value: str, label: str = "identifier") -> str:
    """校验并引用 PostgreSQL 标识符。

    Args:
        value: 待引用的标识符。
        label: 错误信息中的字段名。

    Returns:
        双引号引用后的 PostgreSQL 标识符。
    """
    validate_safe_identifier(value, label)
    return f'"{value}"'


def qualified_table_name(schema_name: str, table_name: str) -> str:
    """生成带 schema 的 PostgreSQL 表名。

    Args:
        schema_name: schema 名称。
        table_name: 表名。

    Returns:
        已校验并引用的 schema.table 名称。
    """
    return f"{quote_identifier(schema_name, 'schema_name')}.{quote_identifier(table_name, 'table_name')}"


def create_schema_statement(schema_name: str) -> str:
    """生成创建 schema 的幂等 DDL。

    Args:
        schema_name: schema 名称。

    Returns:
        CREATE SCHEMA 语句。
    """
    return f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(schema_name, 'schema_name')}"


def create_extension_statement(extension_name: str) -> str:
    """生成创建扩展的幂等 DDL。

    Args:
        extension_name: PostgreSQL 扩展名。

    Returns:
        CREATE EXTENSION 语句。
    """
    validate_safe_identifier(extension_name, "extension_name")
    return f"CREATE EXTENSION IF NOT EXISTS {quote_identifier(extension_name, 'extension_name')}"


def schema_index_extension_statements(settings: IndexStoreSettings) -> list[str]:
    """生成表/列索引结构需要的扩展 DDL。

    Args:
        settings: 索引存储配置。

    Returns:
        扩展创建语句列表。
    """
    statements: list[str] = []
    if settings.enable_bm25:
        statements.append(create_extension_statement("pg_search"))
    if settings.enable_pg_trgm:
        statements.append(create_extension_statement("pg_trgm"))
    if settings.requires_pgvector:
        statements.append(create_extension_statement("vector"))
    return statements


def value_index_extension_statements(settings: IndexStoreSettings) -> list[str]:
    """生成字段值索引结构需要的扩展 DDL。

    Args:
        settings: 索引存储配置。

    Returns:
        扩展创建语句列表。
    """
    statements: list[str] = []
    if settings.enable_bm25:
        statements.append(create_extension_statement("pg_search"))
    if settings.enable_pg_trgm:
        statements.append(create_extension_statement("pg_trgm"))
    if settings.requires_pgvector:
        statements.append(create_extension_statement("vector"))
    return statements


def vector_column_type(dimension: int) -> str:
    """根据配置生成 pgvector 字段类型。

    Args:
        dimension: 向量维度。

    Returns:
        PostgreSQL pgvector 字段类型。
    """
    return f"VECTOR({dimension})"


def assert_vector_column_type_statement(
    *,
    schema_name: str,
    table_name: str,
    column_name: str,
    dimension: int,
) -> str:
    """生成向量字段维度断言语句。

    Args:
        schema_name: PostgreSQL schema 名称。
        table_name: 表名。
        column_name: 向量字段名。
        dimension: 期望向量维度。

    Returns:
        PostgreSQL DO 断言语句。
    """
    validate_safe_identifier(schema_name, "schema_name")
    validate_safe_identifier(table_name, "table_name")
    validate_safe_identifier(column_name, "column_name")
    relation_name = f"{schema_name}.{table_name}"
    expected_type = f"vector({dimension})"
    column_label = f"{relation_name}.{column_name}"
    return f"""
DO $$
DECLARE
    actual_type text;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod)
      INTO actual_type
      FROM pg_attribute a
     WHERE a.attrelid = '{relation_name}'::regclass
       AND a.attname = '{column_name}'
       AND NOT a.attisdropped;

    IF actual_type IS NOT NULL AND actual_type <> '{expected_type}' THEN
        RAISE EXCEPTION 'TableRAG vector column dimension mismatch: {column_label}=%, expected {expected_type}', actual_type;
    END IF;
END;
$$
""".strip()


def build_bm25_index_statement(
    *,
    index_name: str,
    table: str,
    key_field: str,
    text_fields: list[str],
) -> str:
    """生成 pg_search BM25 索引 DDL。

    Args:
        index_name: BM25 索引名。
        table: 已引用的 PostgreSQL 表名。
        key_field: 唯一 key 字段名，必须是索引字段列表第一项。
        text_fields: 参与 BM25 检索的文本字段。

    Returns:
        BM25 索引 DDL。
    """
    quoted_key = quote_identifier(key_field, "bm25_key_field")
    quoted_text_fields = [quote_identifier(field_name, "bm25_field") for field_name in text_fields]
    fields = ", ".join([quoted_key, *quoted_text_fields])
    return f"""
CREATE INDEX IF NOT EXISTS {quote_identifier(index_name, 'index_name')}
ON {table}
USING bm25 ({fields})
WITH (
    key_field = '{key_field}'
)
""".strip()


def build_table_index_statements(settings: IndexStoreSettings) -> list[str]:
    """生成表结构索引表 DDL 语句。

    Args:
        settings: 索引存储配置。

    Returns:
        表结构索引表及其数据库索引 DDL。
    """
    table = qualified_table_name(settings.schema_name, settings.table_index_name)
    embedding_column = (
        f",\n    embedding {vector_column_type(settings.table_embedding_dimension)}"
        if settings.table_embedding_enabled
        else ""
    )
    statements = [
        create_schema_statement(settings.schema_name),
        *schema_index_extension_statements(settings),
        f"""
CREATE TABLE IF NOT EXISTS {table} (
    id BIGSERIAL PRIMARY KEY,
    db_type VARCHAR(50) NOT NULL,
    table_name VARCHAR(128) NOT NULL,
    table_label VARCHAR(256),
    ddl_text TEXT NOT NULL,
    ddl_hash VARCHAR(64),
    schema_summary TEXT NOT NULL{embedding_column},
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (db_type, table_name)
)
""".strip(),
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS id BIGSERIAL",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS db_type VARCHAR(50) NOT NULL DEFAULT 'postgresql'",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS ddl_text TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS ddl_hash VARCHAR(64)",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS schema_summary TEXT NOT NULL DEFAULT ''",
    ]
    if settings.table_embedding_enabled:
        statements.append(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS embedding {vector_column_type(settings.table_embedding_dimension)}"
        )
        statements.append(
            assert_vector_column_type_statement(
                schema_name=settings.schema_name,
                table_name=settings.table_index_name,
                column_name="embedding",
                dimension=settings.table_embedding_dimension,
            )
        )
    if settings.enable_pg_trgm:
        statements.append(
            f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.table_index_name}_schema_summary_trgm_idx', 'index_name')} "
            f"ON {table} USING GIN (schema_summary gin_trgm_ops)"
        )
    if settings.enable_bm25:
        statements.append(
            build_bm25_index_statement(
                index_name=f"{settings.table_index_name}_bm25_idx",
                table=table,
                key_field="id",
                text_fields=["schema_summary"],
            )
        )
    return statements


def build_evidence_index_statements(settings: IndexStoreSettings) -> list[str]:
    """生成 Evidence 业务规则索引表 DDL 语句。

    Args:
        settings: 索引存储配置。

    Returns:
        Evidence 索引表及其数据库索引 DDL。
    """
    table = qualified_table_name(settings.schema_name, settings.evidence_index_name)
    statements = [
        create_schema_statement(settings.schema_name),
        *schema_index_extension_statements(settings),
        f"""
CREATE TABLE IF NOT EXISTS {table} (
    id BIGSERIAL PRIMARY KEY,
    triggers JSONB NOT NULL,
    retrieval_text TEXT NOT NULL,
    evidence_content TEXT NOT NULL,
    evidence_type VARCHAR(50),
    description TEXT,
    status SMALLINT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""".strip(),
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS retrieval_text TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS status SMALLINT DEFAULT 1",
    ]
    if settings.enable_pg_trgm:
        statements.append(
            f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.evidence_index_name}_retrieval_text_trgm_idx', 'index_name')} "
            f"ON {table} USING GIN (retrieval_text gin_trgm_ops)"
        )
    if settings.enable_bm25:
        statements.append(
            build_bm25_index_statement(
                index_name=f"{settings.evidence_index_name}_bm25_idx",
                table=table,
                key_field="id",
                text_fields=["retrieval_text"],
            )
        )
    if settings.evidence_embedding_enabled:
        statements.extend(build_evidence_embedding_statements(settings))
    return statements


def build_evidence_embedding_statements(settings: IndexStoreSettings) -> list[str]:
    """生成 Evidence 可选向量表 DDL。"""
    evidence_table = qualified_table_name(settings.schema_name, settings.evidence_index_name)
    embedding_table = qualified_table_name(settings.schema_name, settings.evidence_embedding_table_name)
    vector_type = vector_column_type(settings.evidence_embedding_dimension)
    return [
        f"""
CREATE TABLE IF NOT EXISTS {embedding_table} (
    id BIGSERIAL PRIMARY KEY,
    evidence_index_id BIGINT NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    embedding {vector_type} NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT {quote_identifier(f'{settings.evidence_embedding_table_name}_evidence_fk', 'constraint_name')}
        FOREIGN KEY (evidence_index_id)
        REFERENCES {evidence_table}(id)
        ON DELETE CASCADE
)
""".strip(),
        assert_vector_column_type_statement(
            schema_name=settings.schema_name,
            table_name=settings.evidence_embedding_table_name,
            column_name="embedding",
            dimension=settings.evidence_embedding_dimension,
        ),
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.evidence_embedding_table_name}_hnsw_cosine_idx', 'index_name')} ON {embedding_table} USING hnsw (embedding vector_cosine_ops)",
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.evidence_embedding_table_name}_hnsw_ip_idx', 'index_name')} ON {embedding_table} USING hnsw (embedding vector_ip_ops)",
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.evidence_embedding_table_name}_hnsw_l2_idx', 'index_name')} ON {embedding_table} USING hnsw (embedding vector_l2_ops)",
    ]


def build_column_index_statements(settings: IndexStoreSettings) -> list[str]:
    """生成列字段索引表 DDL 语句。

    Args:
        settings: 索引存储配置。

    Returns:
        列字段索引表及其数据库索引 DDL。
    """
    table = qualified_table_name(settings.schema_name, settings.column_index_name)
    embedding_column = (
        f",\n    embedding {vector_column_type(settings.column_embedding_dimension)}"
        if settings.column_embedding_enabled
        else ""
    )
    statements = [
        create_schema_statement(settings.schema_name),
        *schema_index_extension_statements(settings),
        f"""
CREATE TABLE IF NOT EXISTS {table} (
    id BIGSERIAL PRIMARY KEY,
    table_name VARCHAR(128) NOT NULL,
    column_name VARCHAR(128) NOT NULL,
    column_comment TEXT,
    column_entities JSONB,
    retrieval_text TEXT NOT NULL{embedding_column},
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (table_name, column_name)
)
""".strip(),
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS id BIGSERIAL",
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS retrieval_text TEXT NOT NULL DEFAULT ''",
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.column_index_name}_column_table_idx', 'index_name')} ON {table} (column_name, table_name)",
    ]
    if settings.column_embedding_enabled:
        statements.append(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS embedding {vector_column_type(settings.column_embedding_dimension)}"
        )
        statements.append(
            assert_vector_column_type_statement(
                schema_name=settings.schema_name,
                table_name=settings.column_index_name,
                column_name="embedding",
                dimension=settings.column_embedding_dimension,
            )
        )
        statements.append(
            f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.column_index_name}_embedding_hnsw_idx', 'index_name')} "
            f"ON {table} USING hnsw (embedding vector_cosine_ops)"
        )
    if settings.enable_pg_trgm:
        statements.append(
            f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.column_index_name}_retrieval_text_trgm_idx', 'index_name')} "
            f"ON {table} USING GIN (retrieval_text gin_trgm_ops)"
        )
    if settings.enable_bm25:
        statements.append(
            build_bm25_index_statement(
                index_name=f"{settings.column_index_name}_bm25_idx",
                table=table,
                key_field="id",
                text_fields=["retrieval_text"],
            )
        )
    return statements


def build_join_graph_statements(settings: IndexStoreSettings) -> list[str]:
    """生成 Join Graph 边表 DDL 语句。

    Args:
        settings: 索引存储配置。

    Returns:
        Join Graph 边表及其数据库索引 DDL。
    """
    table = qualified_table_name(settings.schema_name, settings.join_edge_table_name)
    return [
        create_schema_statement(settings.schema_name),
        f"""
CREATE TABLE IF NOT EXISTS {table} (
    id BIGSERIAL PRIMARY KEY,
    source_table TEXT NOT NULL,
    target_table TEXT NOT NULL,
    join_condition TEXT NOT NULL,
    edge_type TEXT NOT NULL DEFAULT 'inner',
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_table, target_table, join_condition)
)
""".strip(),
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.join_edge_table_name}_source_idx', 'index_name')} ON {table} (source_table)",
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.join_edge_table_name}_target_idx', 'index_name')} ON {table} (target_table)",
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.join_edge_table_name}_source_target_idx', 'index_name')} ON {table} (source_table, target_table)",
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.join_edge_table_name}_weight_idx', 'index_name')} ON {table} (weight)",
    ]


def build_schema_index_statements(settings: IndexStoreSettings) -> list[str]:
    """生成表、列和 Join Graph 索引结构 DDL。

    Args:
        settings: 索引存储配置。

    Returns:
        表、列和 Join Graph 相关 DDL。
    """
    statements: list[str] = []
    statements.extend(build_evidence_index_statements(settings))
    statements.extend(build_table_index_statements(settings))
    statements.extend(build_column_index_statements(settings))
    statements.extend(build_join_graph_statements(settings))
    return _dedupe_statements(statements)


def build_index_metadata_statements(config: TableRAGConfig) -> list[str]:
    """生成索引元数据表和 schema version 写入语句。

    Args:
        config: TableRAG 总配置。

    Returns:
        索引元数据表 DDL 和组件版本 upsert 语句。
    """
    settings = config.index_store
    schema_name = settings.schema_name
    table = qualified_table_name(schema_name, INDEX_METADATA_TABLE_NAME)
    statements = [
        create_schema_statement(schema_name),
        f"""
CREATE TABLE IF NOT EXISTS {table} (
    component TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip(),
    ]
    component_metadata = {
        "evidence_index": {
            "schema_name": settings.schema_name,
            "table_name": settings.evidence_index_name,
            "enable_bm25": settings.enable_bm25,
            "embedding_enabled": settings.evidence_embedding_enabled,
            "embedding_dimension": settings.evidence_embedding_dimension if settings.evidence_embedding_enabled else None,
            "embedding_table_name": settings.evidence_embedding_table_name if settings.evidence_embedding_enabled else None,
        },
        "table_index": {
            "schema_name": settings.schema_name,
            "table_name": settings.table_index_name,
            "enable_bm25": settings.enable_bm25,
            "embedding_enabled": settings.table_embedding_enabled,
            "embedding_dimension": settings.table_embedding_dimension if settings.table_embedding_enabled else None,
            "vector_column_type": (
                vector_column_type(settings.table_embedding_dimension) if settings.table_embedding_enabled else None
            ),
        },
        "column_index": {
            "schema_name": settings.schema_name,
            "table_name": settings.column_index_name,
            "enable_bm25": settings.enable_bm25,
            "embedding_enabled": settings.column_embedding_enabled,
            "embedding_dimension": settings.column_embedding_dimension if settings.column_embedding_enabled else None,
            "vector_column_type": (
                vector_column_type(settings.column_embedding_dimension) if settings.column_embedding_enabled else None
            ),
        },
        "join_graph": {
            "schema_name": settings.schema_name,
            "table_name": settings.join_edge_table_name,
        },
        "field_value_index": {
            "schema_name": settings.schema_name,
            "table_name": settings.field_value_table_name,
            "enable_bm25": settings.enable_bm25,
            "embedding_enabled": settings.field_value_embedding_enabled,
            "embedding_dimension": (
                settings.field_value_embedding_dimension if settings.field_value_embedding_enabled else None
            ),
            "embedding_table_name": (
                settings.field_value_embedding_table_name if settings.field_value_embedding_enabled else None
            ),
        },
    }
    for component, version in required_schema_versions_for_config(config).items():
        metadata = json_dumps(component_metadata.get(component, {})).replace("'", "''")
        statements.append(
            f"""
INSERT INTO {table} (component, schema_version, metadata, updated_at)
VALUES ('{component}', {version}, '{metadata}'::jsonb, now())
ON CONFLICT (component)
DO UPDATE SET
    schema_version = EXCLUDED.schema_version,
    metadata = EXCLUDED.metadata,
    updated_at = now()
""".strip()
        )
    return statements


def build_value_index_statements(settings: IndexStoreSettings) -> list[str]:
    """生成字段值索引表 DDL 语句。

    Args:
        settings: 索引存储配置。

    Returns:
        字段值索引表及其数据库索引 DDL。
    """
    table_name = settings.field_value_table_name
    table = qualified_table_name(settings.schema_name, table_name)
    statements = [
        create_schema_statement(settings.schema_name),
        *value_index_extension_statements(settings),
    ]
    statements.extend(
        [
            f"""
CREATE TABLE IF NOT EXISTS {table} (
    id BIGSERIAL PRIMARY KEY,
    raw_value TEXT NOT NULL,
    table_name VARCHAR(128) NOT NULL,
    column_name VARCHAR(128) NOT NULL,
    aliases JSONB,
    retrieval_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (table_name, column_name, raw_value)
)
""".strip(),
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS retrieval_text TEXT NOT NULL DEFAULT ''",
            f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{table_name}_table_column_idx', 'index_name')} ON {table} (table_name, column_name)",
        ]
    )
    if settings.enable_pg_trgm:
        statements.extend(
            [
                f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{table_name}_retrieval_text_trgm_idx', 'index_name')} ON {table} USING GIN (retrieval_text gin_trgm_ops)",
            ]
        )
    if settings.enable_bm25:
        statements.append(
            build_bm25_index_statement(
                index_name=f"{table_name}_bm25_idx",
                table=table,
                key_field="id",
                text_fields=["retrieval_text"],
            )
        )
    if settings.field_value_embedding_enabled:
        statements.extend(build_field_value_embedding_statements(settings))
    return statements


def build_field_value_embedding_statements(settings: IndexStoreSettings) -> list[str]:
    """生成字段值可选向量表 DDL。"""
    value_table = qualified_table_name(settings.schema_name, settings.field_value_table_name)
    embedding_table = qualified_table_name(settings.schema_name, settings.field_value_embedding_table_name)
    vector_type = vector_column_type(settings.field_value_embedding_dimension)
    return [
        f"""
CREATE TABLE IF NOT EXISTS {embedding_table} (
    id BIGSERIAL PRIMARY KEY,
    value_index_id BIGINT NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    embedding {vector_type} NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT {quote_identifier(f'{settings.field_value_embedding_table_name}_value_fk', 'constraint_name')}
        FOREIGN KEY (value_index_id)
        REFERENCES {value_table}(id)
        ON DELETE CASCADE
)
""".strip(),
        assert_vector_column_type_statement(
            schema_name=settings.schema_name,
            table_name=settings.field_value_embedding_table_name,
            column_name="embedding",
            dimension=settings.field_value_embedding_dimension,
        ),
        f"CREATE INDEX IF NOT EXISTS {quote_identifier(f'{settings.field_value_embedding_table_name}_hnsw_cosine_idx', 'index_name')} ON {embedding_table} USING hnsw (embedding vector_cosine_ops)",
    ]


def build_postgres_index_statements(config: TableRAGConfig) -> list[str]:
    """根据 TableRAG 配置生成完整 PostgreSQL 索引 DDL。

    Args:
        config: TableRAG 总配置。

    Returns:
        按执行顺序排列的幂等 DDL 语句列表。
    """
    settings = config.index_store
    statements = [create_extension_statement(name) for name in sorted(required_extensions_for_config(config))]
    statements.extend(build_index_metadata_statements(config))
    statements.extend(build_evidence_index_statements(settings))
    statements.extend(build_table_index_statements(settings))
    statements.extend(build_column_index_statements(settings))
    statements.extend(build_join_graph_statements(settings))
    statements.extend(build_value_index_statements(settings))
    return _dedupe_statements(statements)


def _dedupe_statements(statements: list[str]) -> list[str]:
    """按顺序去重 DDL 语句。

    Args:
        statements: 原始 DDL 语句列表。

    Returns:
        去重后的 DDL 语句列表。
    """
    seen: set[str] = set()
    deduped: list[str] = []
    for statement in statements:
        normalized = " ".join(statement.split())
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(statement)
    return deduped
