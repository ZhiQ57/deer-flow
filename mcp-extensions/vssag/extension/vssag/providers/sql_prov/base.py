"""Shared SQL Schema provider helpers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from ...vssag_schemas.sql import (
    SQLCollectedFieldValue,
    SQLColumnInfo,
    SQLFieldIndexStatistics,
    SQLFieldValueCollectionCheckpoint,
    SQLForeignKeyInfo,
    SQLGraphEdge,
    SQLSchemaCollection,
    SQLTableSchema,
)


class SQLSchemaProvider(Protocol):
    db_type: str

    async def collect(
        self,
        *,
        exclude_schemas: Sequence[str] = (),
        exclude_tables: Sequence[str] = (),
        value_columns: dict[str, Sequence[str]] | None = None,
        sample_rows: int = 5,
        max_values_per_column: int = 100,
        database_id: int = 0,
        collection_job_id: str = "",
        field_value_config: Any = None,
        collection_checkpoints: Sequence[SQLFieldValueCollectionCheckpoint] = (),
        collect_field_values: bool = True,
        table_schemas: Sequence[SQLTableSchema] | None = None,
        persist_field_value_page: Callable[
            [
                Sequence[SQLCollectedFieldValue],
                SQLFieldIndexStatistics,
                SQLFieldValueCollectionCheckpoint,
            ],
            Awaitable[None],
        ]
        | None = None,
        annotate_field_value_page: Callable[..., Awaitable[list[SQLCollectedFieldValue]]]
        | None = None,
        control_callback: Callable[[dict[str, Any]], Awaitable[Any] | Any]
        | None = None,
    ) -> SQLSchemaCollection:
        ...

    async def close(self) -> None:
        ...


def stable_alias(prefix: str, *parts: str, enabled: bool) -> str:
    """Return a deterministic opaque alias when real names are disabled."""

    value = "::".join(str(part) for part in parts)
    if enabled:
        return str(parts[-1])
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def table_alias(db_type: str, schema_name: str, table_name: str, enabled: bool) -> str:
    return stable_alias("t", db_type, schema_name, table_name, enabled=enabled)


def column_alias(
    db_type: str,
    schema_name: str,
    table_name: str,
    column_name: str,
    enabled: bool,
) -> str:
    # SQL Schema 安全边界只隐藏原始表名，列字段名保持原名，避免增加
    # 模型推理和人工审查负担。
    return str(column_name)


def quote_identifier(value: str, *, dialect: str) -> str:
    """Quote an identifier after rejecting embedded quote characters."""

    if "\x00" in value:
        raise ValueError("SQL identifier contains a NUL byte")
    if dialect == "mysql":
        if "`" in value:
            raise ValueError("SQL identifier contains a backtick")
        return f"`{value}`"
    if '"' in value:
        raise ValueError("SQL identifier contains a double quote")
    return f'"{value}"'


def normalize_ddl(ddl: str) -> str:
    return re.sub(r"\s+", " ", (ddl or "").strip())


def randomize_ddl(
    ddl: str,
    *,
    db_type: str,
    schema_name: str,
    table_name: str,
    enabled: bool,
) -> str:
    if enabled:
        return ddl
    alias = table_alias(db_type, schema_name, table_name, enabled=False)
    return re.sub(
        rf"(?<![\w$]){re.escape(table_name)}(?![\w$])",
        alias,
        ddl or "",
        flags=re.IGNORECASE,
    )


def table_summary(table: SQLTableSchema, *, random_names: bool) -> str:
    table_name = table.random_table_name if random_names else table.raw_table_name
    columns = ", ".join(
        f"{column.name}:{column.data_type}"
        for column in table.columns
    )
    label = table.random_table_name if random_names else (
        table.raw_table_label or table.raw_table_name
    )
    return f"{table_name}({label}): {columns}"


def graph_edges_from_foreign_keys(
    table: SQLTableSchema,
    *,
    allow_real_table_names: bool,
) -> list[SQLGraphEdge]:
    edges: list[SQLGraphEdge] = []
    by_column = {column.name: column for column in table.columns}
    for index, foreign_key in enumerate(table.foreign_keys):
        source_column = by_column.get(foreign_key.column_name)
        source_random_column = (
            source_column.name if source_column else foreign_key.column_name
        )
        target_random_table = table_alias(
            table.db_type,
            foreign_key.referred_schema,
            foreign_key.referred_table,
            allow_real_table_names,
        )
        target_random_column = foreign_key.referred_column
        edges.append(
            SQLGraphEdge(
                id=None,
                db_type=table.db_type,
                raw_schema_name=table.raw_schema_name,
                related_raw_schema_name=foreign_key.referred_schema,
                raw_table_name=table.raw_table_name,
                random_table_name=table.random_table_name,
                raw_column_name=foreign_key.column_name,
                random_column_name=source_random_column,
                related_raw_table_name=foreign_key.referred_table,
                related_random_table_name=target_random_table,
                related_raw_column_name=foreign_key.referred_column,
                related_random_column_name=target_random_column,
                relation_type="physical",
                relation_direction="outgoing",
                cardinality=foreign_key.cardinality,
                join_condition=(
                    f"{table.raw_table_name}.{foreign_key.column_name} = "
                    f"{foreign_key.referred_table}.{foreign_key.referred_column}"
                ),
                evidence_source="foreign_key",
                evidence_content=foreign_key.constraint_name or "database foreign key",
                confidence=1.0,
                status="active",
            )
        )
    return edges


def schema_from_parts(
    *,
    db_type: str,
    schema_name: str,
    table_name: str,
    ddl: str,
    columns: list[SQLColumnInfo],
    foreign_keys: list[SQLForeignKeyInfo],
    indexes=None,
    row_count: int | None = None,
    sample_data: list[dict] | None = None,
    raw_table_label: str = "",
    allow_real_table_names: bool = True,
    database_id: int = 0,
) -> SQLTableSchema:
    random_table_name = table_alias(
        db_type,
        schema_name,
        table_name,
        allow_real_table_names,
    )
    random_columns = [
        column.model_copy(
            update={
                "random_name": column_alias(
                    db_type,
                    schema_name,
                    table_name,
                    column.name,
                    allow_real_table_names,
                )
            }
        )
        for column in columns
    ]
    normalized = normalize_ddl(ddl)
    table = SQLTableSchema(
        database_id=database_id,
        db_type=db_type,
        raw_schema_name=schema_name,
        raw_table_name=table_name,
        random_table_name=random_table_name,
        raw_table_label=raw_table_label or table_name,
        raw_ddl_text=ddl,
        raw_ddl_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        random_ddl_text=randomize_ddl(
            ddl,
            db_type=db_type,
            schema_name=schema_name,
            table_name=table_name,
            enabled=allow_real_table_names,
        ),
        raw_schema_summary="",
        random_schema_summary="",
        columns=random_columns,
        foreign_keys=foreign_keys,
        indexes=indexes or [],
        row_count=row_count,
        sample_rows=sample_data or [],
    )
    table.raw_schema_summary = table_summary(table, random_names=False)
    table.random_schema_summary = table_summary(
        table,
        random_names=not allow_real_table_names,
    )
    return table


def is_excluded(
    schema_name: str,
    table_name: str,
    *,
    exclude_schemas: Sequence[str],
    exclude_tables: Sequence[str],
) -> bool:
    schemas = {str(value).strip().lower() for value in exclude_schemas if str(value).strip()}
    tables = {str(value).strip().lower() for value in exclude_tables if str(value).strip()}
    return schema_name.lower() in schemas or table_name.lower() in tables
