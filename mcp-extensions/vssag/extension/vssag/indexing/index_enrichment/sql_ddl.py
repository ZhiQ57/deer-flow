"""Deterministic SQL DDL descriptions used by the SQL index pipeline."""

from __future__ import annotations

from ...vssag_schemas.sql import SQLSchemaCollection, SQLTableSchema
from ...providers.sql_prov.base import table_summary


def enrich_table(table: SQLTableSchema, *, allow_real_table_names: bool) -> SQLTableSchema:
    """Fill descriptions that can be derived without an LLM."""

    visible_summary = table_summary(
        table,
        random_names=not allow_real_table_names,
    )
    description = table.raw_table_label or table.raw_table_name
    column_lines = [
        f"- {column.name}: "
        f"{column.data_type}"
        + (f" — {column.comment}" if column.comment else "")
        for column in table.columns
    ]
    table = table.model_copy(
        update={
            "raw_schema_summary": table.raw_schema_summary or visible_summary,
            "random_schema_summary": table.random_schema_summary or visible_summary,
            "raw_table_label": description,
        }
    )
    if not table.qa_text:
        table.qa_text = (
            f"# {table.random_table_name if not allow_real_table_names else table.raw_table_name}\n\n"
            f"{visible_summary}\n\n"
            + "\n".join(column_lines)
            + "\n"
        )
    return table


def enrich_collection(
    collection: SQLSchemaCollection,
    *,
    allow_real_table_names: bool,
) -> SQLSchemaCollection:
    return collection.model_copy(
        update={
            "tables": [
                enrich_table(table, allow_real_table_names=allow_real_table_names)
                for table in collection.tables
            ]
        }
    )
