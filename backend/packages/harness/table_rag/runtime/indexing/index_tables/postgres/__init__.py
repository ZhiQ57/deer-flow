"""PostgreSQL 索引生命周期运行时实现模块。"""

from .ddl import (
    build_column_index_statements,
    build_evidence_index_statements,
    build_join_graph_statements,
    build_postgres_index_statements,
    build_schema_index_statements,
    build_table_index_statements,
    build_value_index_statements,
)
from .initializer import PostgresIndexInitializer, PostgresIndexSchemaManager, create_postgres_indexes
from .requirements import (
    PostgresIndexTableRequirement,
    PostgresRuntimeRequirements,
    build_postgres_runtime_requirements,
    required_extensions_for_config,
    required_tables_for_config,
)

__all__ = [
    "PostgresIndexInitializer",
    "PostgresIndexSchemaManager",
    "PostgresIndexTableRequirement",
    "PostgresRuntimeRequirements",
    "build_column_index_statements",
    "build_evidence_index_statements",
    "build_join_graph_statements",
    "build_postgres_index_statements",
    "build_postgres_runtime_requirements",
    "build_schema_index_statements",
    "build_table_index_statements",
    "build_value_index_statements",
    "create_postgres_indexes",
    "required_extensions_for_config",
    "required_tables_for_config",
]
