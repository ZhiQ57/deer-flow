"""SQLite Schema provider used for local deployments and deterministic tests."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ...vssag_schemas.sql import (
    SQLCollectedFieldValue,
    SQLColumnInfo,
    SQLFieldIndexStatistics,
    SQLFieldValueCollectionCheckpoint,
    SQLForeignKeyInfo,
    SQLGraphEdge,
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


class SQLiteSchemaProvider:
    db_type = "sqlite"

    def __init__(
        self,
        dsn: str,
        *,
        allow_real_table_names: bool = True,
    ):
        self.dsn = dsn or ":memory:"
        self.allow_real_table_names = allow_real_table_names
        self.path = self._parse_path(self.dsn)

    @staticmethod
    def _parse_path(dsn: str) -> str:
        if dsn.startswith("sqlite:///"):
            parsed = urlparse(dsn)
            path = unquote(parsed.path)
            if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
                path = path[1:]
            return path or ":memory:"
        if dsn.startswith("sqlite://"):
            return unquote(dsn[len("sqlite://") :]) or ":memory:"
        return dsn

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

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
        loop = (
            asyncio.get_running_loop()
            if persist_field_value_page or annotate_field_value_page
            else None
        )
        return await asyncio.to_thread(
            self._collect_sync,
            exclude_schemas,
            exclude_tables,
            value_columns or {},
            sample_rows,
            max_values_per_column,
            database_id,
            collection_job_id,
            field_value_config,
            collection_checkpoints,
            collect_field_values,
            table_schemas,
            persist_field_value_page,
            annotate_field_value_page,
            control_callback,
            loop,
        )

    def _collect_sync(
        self,
        exclude_schemas: Sequence[str],
        exclude_tables: Sequence[str],
        value_columns: dict[str, Sequence[str]],
        sample_rows: int,
        max_values_per_column: int,
        database_id: int,
        collection_job_id: str,
        field_value_config,
        collection_checkpoints: Sequence[SQLFieldValueCollectionCheckpoint],
        collect_field_values: bool,
        table_schemas: Sequence[SQLTableSchema] | None,
        persist_field_value_page,
        annotate_field_value_page,
        control_callback,
        loop,
    ) -> SQLSchemaCollection:
        collection = SQLSchemaCollection(
            db_type=self.db_type,
            database_id=database_id,
            collection_job_id=collection_job_id,
        )
        with self._connect() as connection:
            query_timeout_seconds = max(
                0.001,
                float(
                    getattr(
                        field_value_config,
                        "query_timeout_seconds",
                        30.0,
                    )
                ),
            )
            query_deadline = [0.0]

            def interrupt_long_query() -> int:
                """在 SQLite VM 超过当前查询期限时中断执行。

                Returns:
                    超时返回 1，否则返回 0。
                """

                return int(
                    query_deadline[0] > 0
                    and time.monotonic() >= query_deadline[0]
                )

            connection.set_progress_handler(interrupt_long_query, 1000)
            connection.execute("BEGIN")
            query_deadline[0] = time.monotonic() + query_timeout_seconds
            if table_schemas is None:
                table_rows = connection.execute(
                    """
                    SELECT name, sql
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                ).fetchall()
            else:
                table_rows = [
                    {
                        "name": table.raw_table_name,
                        "sql": table.raw_ddl_text,
                        "table": table,
                    }
                    for table in table_schemas
                ]
            query_deadline[0] = 0.0
            for row in table_rows:
                schema_name = "main"
                table_name = str(row["name"])
                if is_excluded(
                    schema_name,
                    table_name,
                    exclude_schemas=exclude_schemas,
                    exclude_tables=exclude_tables,
                ):
                    continue
                existing_table = row.get("table") if isinstance(row, dict) else None
                if existing_table is None:
                    query_deadline[0] = (
                        time.monotonic() + query_timeout_seconds
                    )
                    table = self._collect_table(
                        connection,
                        schema_name,
                        table_name,
                        row["sql"] or "",
                        sample_rows=sample_rows,
                    )
                    query_deadline[0] = 0.0
                else:
                    table = existing_table
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
                values, statistics = self._collect_values(
                        connection,
                        table,
                        value_columns.get(table_name) or value_columns.get(
                            f"{schema_name}.{table_name}"
                        ),
                        max_values_per_column=max_values_per_column,
                        database_id=database_id,
                        collection_job_id=collection_job_id,
                        field_value_config=field_value_config,
                        collection_checkpoints=collection_checkpoints,
                        persist_field_value_page=persist_field_value_page,
                        annotate_field_value_page=annotate_field_value_page,
                        control_callback=control_callback,
                        loop=loop,
                        query_deadline=query_deadline,
                        query_timeout_seconds=query_timeout_seconds,
                    )
                collection.collected_field_values.extend(values)
                collection.field_statistics.extend(statistics)
        return collection

    def _collect_table(
        self,
        connection: sqlite3.Connection,
        schema_name: str,
        table_name: str,
        ddl: str,
        *,
        sample_rows: int,
    ):
        table_ident = quote_identifier(table_name, dialect="sqlite")
        columns = []
        for row in connection.execute(f"PRAGMA table_info({table_ident})"):
            columns.append(
                SQLColumnInfo(
                    name=str(row["name"]),
                    random_name="",
                    data_type=str(row["type"] or ""),
                    nullable=not bool(row["notnull"]),
                    default=str(row["dflt_value"]) if row["dflt_value"] is not None else None,
                    primary_key=bool(row["pk"]),
                    ordinal_position=int(row["cid"]) + 1,
                )
            )
        foreign_keys = []
        for row in connection.execute(f"PRAGMA foreign_key_list({table_ident})"):
            foreign_keys.append(
                SQLForeignKeyInfo(
                    column_name=str(row["from"]),
                    referred_schema="main",
                    referred_table=str(row["table"]),
                    referred_column=str(row["to"]),
                    constraint_name=None,
                    cardinality="many_to_one",
                )
            )
        indexes = []
        for row in connection.execute(f"PRAGMA index_list({table_ident})"):
            index_name = str(row["name"])
            index_columns = [
                str(index_row["name"])
                for index_row in connection.execute(
                    f"PRAGMA index_info({quote_identifier(index_name, dialect='sqlite')})"
                ).fetchall()
                if index_row["name"] is not None
            ]
            indexes.append(
                SQLIndexInfo(
                    name=index_name,
                    columns=index_columns,
                    unique=bool(row["unique"]),
                    index_type="sqlite",
                )
            )
        count = connection.execute(f"SELECT COUNT(*) AS count FROM {table_ident}").fetchone()
        rows = connection.execute(
            f"SELECT * FROM {table_ident} LIMIT ?",
            (max(0, int(sample_rows)),),
        ).fetchall()
        sample_data = [dict(row) for row in rows]
        return schema_from_parts(
            db_type=self.db_type,
            schema_name=schema_name,
            table_name=table_name,
            ddl=ddl,
            columns=columns,
            foreign_keys=foreign_keys,
            indexes=indexes,
            row_count=int(count["count"]) if count else None,
            sample_data=sample_data,
            allow_real_table_names=self.allow_real_table_names,
        )

    def _collect_values(
        self,
        connection: sqlite3.Connection,
        table,
        selected_columns: Sequence[str] | None,
        *,
        max_values_per_column: int,
        database_id: int,
        collection_job_id: str,
        field_value_config,
        collection_checkpoints: Sequence[SQLFieldValueCollectionCheckpoint],
        persist_field_value_page,
        annotate_field_value_page,
        control_callback,
        loop,
        query_deadline: list[float],
        query_timeout_seconds: float,
    ) -> tuple[list[SQLCollectedFieldValue], list]:
        config = field_value_config
        mode = str(getattr(config, "mode", "manual")).lower()
        manual_columns = list(selected_columns or [])
        columns = (
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
        table_ident = quote_identifier(table.raw_table_name, dialect="sqlite")
        for column_name in columns:
            column = known.get(str(column_name))
            if column is None:
                continue
            column_ident = quote_identifier(column.name, dialect="sqlite")
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
                query_deadline[0] = (
                    time.monotonic() + query_timeout_seconds
                )
                statistics_row = connection.execute(
                    f"""
                    SELECT COUNT(*) AS total_count,
                           COUNT({column_ident}) AS non_null_count,
                           COUNT(DISTINCT {column_ident}) AS distinct_count
                    FROM {table_ident}
                    """
                ).fetchone()
                query_deadline[0] = (
                    time.monotonic() + query_timeout_seconds
                )
                sample_values = [
                    str(row["value"])
                    for row in connection.execute(
                        f"""
                        SELECT {column_ident} AS value
                        FROM {table_ident}
                        WHERE {column_ident} IS NOT NULL
                        ORDER BY CAST({column_ident} AS TEXT)
                        LIMIT ?
                        """,
                        (
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
                        ),
                    ).fetchall()
                ]
                query_deadline[0] = 0.0
                frequencies: dict[str, int] = {}
                for item in sample_values:
                    category = classify_field_value(item)
                    frequencies[category] = (
                        frequencies.get(category, 0) + 1
                    )
                denominator = max(1, len(sample_values))
                pattern_distribution = {
                    key: value / denominator
                    for key, value in frequencies.items()
                }
                statistics = calculate_field_index_statistics(
                    collection_job_id=collection_job_id,
                    database_id=database_id,
                    db_type=self.db_type,
                    schema_name=table.raw_schema_name,
                    table_name=table.raw_table_name,
                    column=column,
                    row_count=int(statistics_row["total_count"] or 0),
                    non_null_count=int(
                        statistics_row["non_null_count"] or 0
                    ),
                    distinct_count=int(
                        statistics_row["distinct_count"] or 0
                    ),
                    lengths=[len(item) for item in sample_values],
                    pattern_distribution=pattern_distribution,
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
                    if control_callback is not None:
                        future = asyncio.run_coroutine_threadsafe(
                            control_callback(
                                {
                                    "stage": "field_values_page",
                                    "schema_name": table.raw_schema_name,
                                    "table_name": table.raw_table_name,
                                    "column_name": column.name,
                                    "processed_values": processed_values,
                                }
                            ),
                            loop,
                        )
                        future.result()
                    future = asyncio.run_coroutine_threadsafe(
                        persist_field_value_page(
                            (),
                            statistics,
                            SQLFieldValueCollectionCheckpoint(
                                collection_job_id=collection_job_id,
                                schema_name=table.raw_schema_name,
                                raw_table_name=table.raw_table_name,
                                column_name=column.name,
                                status="completed",
                            ),
                        ),
                        loop,
                    )
                    future.result()
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
                query_deadline[0] = (
                    time.monotonic() + query_timeout_seconds
                )
                if cursor_text is None:
                    page_rows = connection.execute(
                        f"""
                        SELECT CAST({column_ident} AS TEXT) AS value,
                               COUNT(*) AS value_count
                        FROM {table_ident}
                        WHERE {column_ident} IS NOT NULL
                        GROUP BY CAST({column_ident} AS TEXT)
                        ORDER BY CAST({column_ident} AS TEXT)
                        LIMIT ?
                        """,
                        (fetch_size,),
                    ).fetchall()
                else:
                    page_rows = connection.execute(
                        f"""
                        SELECT CAST({column_ident} AS TEXT) AS value,
                               COUNT(*) AS value_count
                        FROM {table_ident}
                        WHERE {column_ident} IS NOT NULL
                          AND CAST({column_ident} AS TEXT) > ?
                        GROUP BY CAST({column_ident} AS TEXT)
                        ORDER BY CAST({column_ident} AS TEXT)
                        LIMIT ?
                        """,
                        (cursor_text, fetch_size),
                    ).fetchall()
                query_deadline[0] = 0.0
                if not page_rows:
                    if persist_field_value_page is not None:
                        future = asyncio.run_coroutine_threadsafe(
                            persist_field_value_page(
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
                            ),
                            loop,
                        )
                        future.result()
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
                    future = asyncio.run_coroutine_threadsafe(
                        annotate_field_value_page(
                            statistics,
                            page_values,
                            random_table_name=table.random_table_name,
                        ),
                        loop,
                    )
                    page_values = future.result()
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
                        future = asyncio.run_coroutine_threadsafe(
                            control_callback(
                                {
                                    "stage": "field_values_page",
                                    "schema_name": table.raw_schema_name,
                                    "table_name": table.raw_table_name,
                                    "column_name": column.name,
                                    "processed_values": processed_values,
                                }
                            ),
                            loop,
                        )
                        future.result()
                    future = asyncio.run_coroutine_threadsafe(
                        persist_field_value_page(
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
                        ),
                        loop,
                    )
                    future.result()
                if completed:
                    break
                page_interval = float(
                    getattr(config, "page_interval_seconds", 0.0)
                )
                if page_interval > 0:
                    time.sleep(page_interval)
            if (
                statistics.selection_decision == "uncertain"
                and not llm_has_pending
            ):
                statistics.selection_decision = "llm_reviewed"
                statistics.rejection_reason = ""
            statistics.values_truncated = truncated
        return merge_collected_field_values(values), statistics_rows

    async def close(self) -> None:
        return None
