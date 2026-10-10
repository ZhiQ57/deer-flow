"""PostgreSQL Schema provider.

``asyncpg`` is intentionally optional.  The provider only opens a connection
when PostgreSQL is selected in configuration, while SQLite remains available
for local and unit-test execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from ...vssag_schemas.sql import (
    SQLCollectedFieldValue,
    SQLColumnInfo,
    SQLFieldIndexStatistics,
    SQLFieldValueCollectionCheckpoint,
    SQLForeignKeyInfo,
    SQLIndexInfo,
    SQLSchemaCollection,
    SQLTableSchema,
)
from ...indexing.field_value_index_rules import (
    build_collected_field_value,
    calculate_field_index_statistics,
    classify_field_value,
    merge_collected_field_values,
)
from .base import (
    graph_edges_from_foreign_keys,
    is_excluded,
    quote_identifier,
    schema_from_parts,
)


class PostgreSQLSchemaProvider:
    db_type = "postgresql"

    def __init__(
        self,
        dsn: str,
        *,
        allow_real_table_names: bool = True,
    ):
        self.dsn = (dsn or "").replace("postgresql+asyncpg://", "postgresql://", 1)
        self.allow_real_table_names = allow_real_table_names
        self._connection = None

    async def _connect(self):
        if self._connection is not None:
            return self._connection
        try:
            import asyncpg
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "PostgreSQL SQL Schema 采集需要 asyncpg，请安装 "
                "ownmindforge-vssag[postgres]"
            ) from exc
        if not self.dsn:
            raise RuntimeError("PostgreSQL SQL Schema 采集缺少 target_sources.dsn")
        self._connection = await asyncpg.connect(self.dsn)
        return self._connection

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
        field_value_config=None,
        collection_checkpoints: Sequence[SQLFieldValueCollectionCheckpoint] = (),
        collect_field_values: bool = True,
        table_schemas: Sequence[SQLTableSchema] | None = None,
        persist_field_value_page=None,
        annotate_field_value_page=None,
        control_callback=None,
    ) -> SQLSchemaCollection:
        connection = await self._connect()
        timeout_milliseconds = max(
            1,
            int(
                float(
                    getattr(
                        field_value_config,
                        "query_timeout_seconds",
                        30.0,
                    )
                )
                * 1000
            ),
        )
        async with connection.transaction(
            isolation="repeatable_read",
            readonly=True,
        ):
            await connection.execute(
                f"SET LOCAL statement_timeout = {timeout_milliseconds}"
            )
            excluded_schemas = {
                str(value).lower() for value in exclude_schemas
            }
            if table_schemas is None:
                rows = await connection.fetch(
                    """
                    SELECT n.nspname AS schema_name,
                           c.relname AS table_name,
                           obj_description(c.oid, 'pg_class') AS table_comment,
                           GREATEST(c.reltuples, 0)::BIGINT AS row_count
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relkind IN ('r', 'p', 'v', 'm')
                      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY n.nspname, c.relname
                    """
                )
            else:
                rows = [
                    {
                        "schema_name": table.raw_schema_name,
                        "table_name": table.raw_table_name,
                        "table_comment": table.raw_table_label,
                        "row_count": table.row_count,
                        "table": table,
                    }
                    for table in table_schemas
                ]
            collection = SQLSchemaCollection(
                db_type=self.db_type,
                database_id=database_id,
                collection_job_id=collection_job_id,
            )
            for row in rows:
                schema_name = str(row["schema_name"])
                table_name = str(row["table_name"])
                if is_excluded(
                    schema_name,
                    table_name,
                    exclude_schemas=excluded_schemas,
                    exclude_tables=exclude_tables,
                ):
                    continue
                existing_table = row.get("table") if isinstance(row, dict) else None
                table = existing_table or await self._collect_table(
                    connection,
                    schema_name,
                    table_name,
                    str(row["table_comment"] or ""),
                    int(row["row_count"] or 0),
                    value_columns=value_columns or {},
                    sample_rows=sample_rows,
                    max_values_per_column=max_values_per_column,
                )
                table = table.model_copy(update={"database_id": database_id})
                collection.tables.append(table)
                if existing_table is None:
                    collection.graph_edges.extend(
                        graph_edges_from_foreign_keys(
                            table,
                            allow_real_table_names=self.allow_real_table_names,
                        )
                    )
                if not collect_field_values:
                    continue
                values, statistics = await self._collect_values(
                    connection,
                    table,
                    value_columns=(value_columns or {}).get(
                        f"{schema_name}.{table_name}"
                    )
                    or (value_columns or {}).get(table_name),
                    max_values_per_column=max_values_per_column,
                    database_id=database_id,
                    collection_job_id=collection_job_id,
                    field_value_config=field_value_config,
                    collection_checkpoints=collection_checkpoints,
                    persist_field_value_page=persist_field_value_page,
                    annotate_field_value_page=annotate_field_value_page,
                    control_callback=control_callback,
                )
                collection.collected_field_values.extend(values)
                collection.field_statistics.extend(statistics)
        return collection

    async def _collect_table(
        self,
        connection,
        schema_name: str,
        table_name: str,
        table_comment: str,
        row_count: int,
        *,
        value_columns: dict[str, Sequence[str]],
        sample_rows: int,
        max_values_per_column: int,
    ):
        columns_rows = await connection.fetch(
            """
            SELECT c.column_name,
                   c.data_type,
                   c.udt_name,
                   c.is_nullable,
                   c.column_default,
                   c.ordinal_position,
                   d.description AS column_comment
            FROM information_schema.columns c
            LEFT JOIN pg_catalog.pg_statio_all_tables st
              ON st.schemaname = c.table_schema
             AND st.relname = c.table_name
            LEFT JOIN pg_catalog.pg_description d
              ON d.objoid = st.relid
             AND d.objsubid = c.ordinal_position
            WHERE c.table_schema = $1 AND c.table_name = $2
            ORDER BY c.ordinal_position
            """,
            schema_name,
            table_name,
        )
        primary_rows = await connection.fetch(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
             AND tc.table_name = kcu.table_name
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = $1
              AND tc.table_name = $2
            """,
            schema_name,
            table_name,
        )
        primary = {str(row["column_name"]) for row in primary_rows}
        columns = [
            SQLColumnInfo(
                name=str(row["column_name"]),
                random_name="",
                data_type=str(row["udt_name"] or row["data_type"] or ""),
                nullable=str(row["is_nullable"]).upper() == "YES",
                default=str(row["column_default"])
                if row["column_default"] is not None
                else None,
                primary_key=str(row["column_name"]) in primary,
                ordinal_position=int(row["ordinal_position"]),
                comment=str(row["column_comment"] or ""),
            )
            for row in columns_rows
        ]
        foreign_rows = await connection.fetch(
            """
            SELECT tc.constraint_name,
                   kcu.column_name,
                   ccu.table_schema AS referred_schema,
                   ccu.table_name AS referred_table,
                   ccu.column_name AS referred_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
             AND tc.table_name = kcu.table_name
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = $1
              AND tc.table_name = $2
            ORDER BY tc.constraint_name, kcu.ordinal_position
            """,
            schema_name,
            table_name,
        )
        foreign_keys = [
            SQLForeignKeyInfo(
                column_name=str(row["column_name"]),
                referred_schema=str(row["referred_schema"]),
                referred_table=str(row["referred_table"]),
                referred_column=str(row["referred_column"]),
                constraint_name=str(row["constraint_name"]),
                cardinality="many_to_one",
            )
            for row in foreign_rows
        ]
        index_rows = await connection.fetch(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = $1 AND tablename = $2
            ORDER BY indexname
            """,
            schema_name,
            table_name,
        )
        indexes = [
            SQLIndexInfo(
                name=str(row["indexname"]),
                columns=[],
                unique=" UNIQUE " in str(row["indexdef"]).upper(),
                index_type=str(row["indexdef"]),
            )
            for row in index_rows
        ]
        ddl_lines = [
            f"CREATE TABLE {quote_identifier(schema_name, dialect='postgresql')}."
            f"{quote_identifier(table_name, dialect='postgresql')} ("
        ]
        for column in columns:
            nullable = "" if column.nullable else " NOT NULL"
            default = f" DEFAULT {column.default}" if column.default else ""
            ddl_lines.append(
                f"  {quote_identifier(column.name, dialect='postgresql')} "
                f"{column.data_type}{nullable}{default},"
            )
        for foreign_key in foreign_keys:
            ddl_lines.append(
                "  FOREIGN KEY "
                f"({quote_identifier(foreign_key.column_name, dialect='postgresql')}) "
                "REFERENCES "
                f"{quote_identifier(foreign_key.referred_schema, dialect='postgresql')}."
                f"{quote_identifier(foreign_key.referred_table, dialect='postgresql')}("
                f"{quote_identifier(foreign_key.referred_column, dialect='postgresql')}) ,"
            )
        if ddl_lines[-1].endswith(","):
            ddl_lines[-1] = ddl_lines[-1][:-1]
        ddl_lines.append(");")
        ddl = "\n".join(ddl_lines)
        quoted_schema = quote_identifier(schema_name, dialect="postgresql")
        quoted_table = quote_identifier(table_name, dialect="postgresql")
        sample = await connection.fetch(
            f"SELECT * FROM {quoted_schema}.{quoted_table} LIMIT $1",
            max(0, int(sample_rows)),
        )
        sample_data = [dict(row) for row in sample]
        return schema_from_parts(
            db_type=self.db_type,
            schema_name=schema_name,
            table_name=table_name,
            ddl=ddl,
            columns=columns,
            foreign_keys=foreign_keys,
            indexes=indexes,
            row_count=row_count,
            sample_data=sample_data,
            raw_table_label=table_comment,
            allow_real_table_names=self.allow_real_table_names,
        )

    async def _collect_values(
        self,
        connection,
        table,
        *,
        value_columns: Sequence[str] | None,
        max_values_per_column: int,
        database_id: int,
        collection_job_id: str,
        field_value_config,
        collection_checkpoints: Sequence[SQLFieldValueCollectionCheckpoint],
        persist_field_value_page,
        annotate_field_value_page,
        control_callback,
    ) -> tuple[list[SQLCollectedFieldValue], list]:
        config = field_value_config
        mode = str(getattr(config, "mode", "manual")).lower()
        manual_columns = list(value_columns or [])
        selected = (
            manual_columns
            if mode == "manual" and manual_columns
            else [column.name for column in table.columns]
            if mode == "auto"
            else manual_columns
        )
        known = {column.name: column for column in table.columns}
        values: list[SQLCollectedFieldValue] = []
        statistics_rows = []
        checkpoints = {
            (
                checkpoint.schema_name,
                checkpoint.raw_table_name,
                checkpoint.column_name,
            ): checkpoint
            for checkpoint in collection_checkpoints
        }
        schema_ident = quote_identifier(table.raw_schema_name, dialect="postgresql")
        table_ident = quote_identifier(table.raw_table_name, dialect="postgresql")
        for column_name in selected:
            column = known.get(str(column_name))
            if column is None:
                continue
            column_ident = quote_identifier(column.name, dialect="postgresql")
            if mode == "manual":
                statistics = SQLFieldIndexStatistics(
                    collection_job_id=collection_job_id,
                    database_id=database_id,
                    db_type=self.db_type,
                    schema_name=table.raw_schema_name,
                    raw_table_name=table.raw_table_name,
                    column_name=column.name,
                    data_type=column.data_type,
                    comment=column.comment,
                    row_count=table.row_count,
                    field_score=100.0,
                    selection_decision="manual",
                )
            else:
                statistics_row = await connection.fetchrow(
                    f"""
                    SELECT COUNT(*) AS total_count,
                           COUNT({column_ident}) AS non_null_count,
                           COUNT(DISTINCT {column_ident}) AS distinct_count
                    FROM {schema_ident}.{table_ident}
                    """
                )
                sample_rows = await connection.fetch(
                    f"""
                    SELECT {column_ident}::text AS value
                    FROM {schema_ident}.{table_ident}
                    WHERE {column_ident} IS NOT NULL
                    ORDER BY {column_ident}::text
                    LIMIT $1
                    """,
                    max(
                        1,
                        int(
                            getattr(
                                config,
                                "statistics_sample_rows",
                                max_values_per_column,
                            )
                        ),
                    ),
                )
                sample_values = [str(row["value"]) for row in sample_rows]
                frequencies: dict[str, int] = {}
                for item in sample_values:
                    category = classify_field_value(item)
                    frequencies[category] = frequencies.get(category, 0) + 1
                denominator = max(1, len(sample_values))
                statistics = calculate_field_index_statistics(
                    collection_job_id=collection_job_id,
                    database_id=database_id,
                    db_type=self.db_type,
                    schema_name=table.raw_schema_name,
                    table_name=table.raw_table_name,
                    column=column,
                    row_count=table.row_count,
                    non_null_count=int(
                        statistics_row["non_null_count"] or 0
                    ),
                    distinct_count=int(
                        statistics_row["distinct_count"] or 0
                    ),
                    lengths=[len(item) for item in sample_values],
                    pattern_distribution={
                        key: value / denominator
                        for key, value in frequencies.items()
                    },
                    threshold=float(
                        getattr(config, "field_score_threshold", 75.0)
                    ),
                    uncertain_min=float(
                        getattr(config, "uncertain_score_min", 45.0)
                    ),
                )
                if statistics.distinct_count > int(
                    getattr(config, "max_distinct_values", 10000)
                ):
                    statistics.selection_decision = "rejected"
                    statistics.rejection_reason = (
                        "distinct_count_above_limit"
                    )
            statistics_rows.append(statistics)
            checkpoint = checkpoints.get(
                (
                    table.raw_schema_name,
                    table.raw_table_name,
                    column.name,
                )
            )
            processed_values = (
                checkpoint.processed_values if checkpoint is not None else 0
            )
            if checkpoint is not None and checkpoint.status == "completed":
                continue
            if (
                mode == "auto"
                and statistics.selection_decision == "uncertain"
                and annotate_field_value_page is None
            ):
                statistics.rejection_reason = "llm_annotation_disabled"
            if (
                mode == "auto"
                and statistics.selection_decision
                not in {"candidate", "uncertain"}
            ) or (
                mode == "auto"
                and statistics.selection_decision == "uncertain"
                and annotate_field_value_page is None
            ):
                if persist_field_value_page is not None:
                    await persist_field_value_page(
                        (),
                        statistics,
                        SQLFieldValueCollectionCheckpoint(
                            collection_job_id=collection_job_id,
                            schema_name=table.raw_schema_name,
                            raw_table_name=table.raw_table_name,
                            column_name=column.name,
                            status="completed",
                        ),
                    )
                continue
            effective_limit = min(
                max(1, int(max_values_per_column)),
                max(1, int(getattr(config, "max_distinct_values", 10000))),
            )
            page_size = min(
                effective_limit,
                max(1, int(getattr(config, "page_size", effective_limit))),
            )
            llm_has_pending = False
            processed_values = (
                checkpoint.processed_values if checkpoint is not None else 0
            )
            cursor_text = (
                checkpoint.last_raw_value
                if checkpoint is not None and checkpoint.last_raw_value
                else None
            )
            truncated = False
            while processed_values < effective_limit:
                remaining = effective_limit - processed_values
                page_capacity = min(page_size, remaining)
                fetch_size = page_capacity + 1
                if cursor_text is None:
                    page_rows = await connection.fetch(
                        f"""
                        SELECT {column_ident}::text AS value, COUNT(*) AS value_count
                        FROM {schema_ident}.{table_ident}
                        WHERE {column_ident} IS NOT NULL
                        GROUP BY {column_ident}::text
                        ORDER BY {column_ident}::text
                        LIMIT $1
                        """,
                        fetch_size,
                    )
                else:
                    page_rows = await connection.fetch(
                        f"""
                        SELECT {column_ident}::text AS value, COUNT(*) AS value_count
                        FROM {schema_ident}.{table_ident}
                        WHERE {column_ident} IS NOT NULL
                          AND {column_ident}::text > $1
                        GROUP BY {column_ident}::text
                        ORDER BY {column_ident}::text
                        LIMIT $2
                        """,
                        cursor_text,
                        fetch_size,
                    )
                if not page_rows:
                    if persist_field_value_page is not None:
                        await persist_field_value_page(
                            (),
                            statistics,
                            SQLFieldValueCollectionCheckpoint(
                                collection_job_id=collection_job_id,
                                schema_name=table.raw_schema_name,
                                raw_table_name=table.raw_table_name,
                                column_name=column.name,
                                last_raw_value=cursor_text or "",
                                processed_values=processed_values,
                                status="completed",
                            ),
                        )
                    break
                has_more = len(page_rows) > page_capacity
                if has_more:
                    page_rows = page_rows[:page_capacity]
                page_values: list[SQLCollectedFieldValue] = []
                for row in page_rows:
                    value = build_collected_field_value(
                        raw_value=row["value"],
                        value_count=int(row["value_count"] or 0),
                        database_id=database_id,
                        table=table,
                        column=column,
                        max_length=int(getattr(config, "max_value_length", 200)),
                        hard_reject_sensitive=bool(
                            getattr(config, "hard_reject_sensitive", True)
                        ),
                        patterns=tuple(getattr(config, "value_patterns", ())),
                    )
                    if value is not None:
                        page_values.append(value)
                page_values = merge_collected_field_values(page_values)
                if (
                    statistics.selection_decision == "uncertain"
                    and annotate_field_value_page is not None
                ):
                    page_values = await annotate_field_value_page(
                        statistics,
                        page_values,
                        random_table_name=table.random_table_name,
                    )
                    if any(
                        item.annotation is not None
                        and item.annotation.review_status == "pending"
                        for item in page_values
                    ):
                        llm_has_pending = True
                        statistics.rejection_reason = "llm_review_pending"
                values.extend(
                    item
                    for item in page_values
                    if item.annotation is None or item.annotation.should_index
                )
                processed_values += len(page_rows)
                cursor_text = str(page_rows[-1]["value"])
                truncated = has_more and processed_values >= effective_limit
                completed = not has_more or truncated
                statistics.values_truncated = truncated
                if persist_field_value_page is not None:
                    if control_callback is not None:
                        result = control_callback(
                            {
                                "stage": "field_values_page",
                                "schema_name": table.raw_schema_name,
                                "table_name": table.raw_table_name,
                                "column_name": column.name,
                                "processed_values": processed_values,
                            }
                        )
                        if asyncio.iscoroutine(result):
                            await result
                    await persist_field_value_page(
                        page_values,
                        statistics,
                        SQLFieldValueCollectionCheckpoint(
                            collection_job_id=collection_job_id,
                            schema_name=table.raw_schema_name,
                            raw_table_name=table.raw_table_name,
                            column_name=column.name,
                            last_raw_value=cursor_text,
                            processed_values=processed_values,
                            status="completed" if completed else "running",
                        ),
                    )
                if completed:
                    break
                page_interval = float(
                    getattr(config, "page_interval_seconds", 0.0)
                )
                if page_interval > 0:
                    await asyncio.sleep(page_interval)
            if (
                statistics.selection_decision == "uncertain"
                and not llm_has_pending
            ):
                statistics.selection_decision = "llm_reviewed"
                statistics.rejection_reason = ""
            statistics.values_truncated = truncated
        return merge_collected_field_values(values), statistics_rows

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
