"""MySQL Schema provider using the optional ``aiomysql`` driver."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from urllib.parse import urlparse

from ...vssag_schemas.sql import (
    SQLCollectedFieldValue,
    SQLColumnInfo,
    SQLFieldIndexStatistics,
    SQLFieldValueCollectionCheckpoint,
    SQLForeignKeyInfo,
    SQLIndexInfo,
    SQLSchemaCollection,
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


class MySQLSchemaProvider:
    db_type = "mysql"

    def __init__(self, dsn: str, *, allow_real_table_names: bool = True):
        self.dsn = dsn or ""
        self.allow_real_table_names = allow_real_table_names
        parsed = urlparse(self.dsn.replace("mysql+aiomysql://", "mysql://", 1))
        self.database = (parsed.path or "/").lstrip("/") or ""
        self._pool = None

    async def _connect(self):
        try:
            import aiomysql
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "MySQL SQL Schema 采集需要 aiomysql，请安装 "
                "ownmindforge-vssag[mysql]"
            ) from exc
        if not self.dsn:
            raise RuntimeError("MySQL SQL Schema 采集缺少 target_sources.dsn")
        if self._pool is None:
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(self.dsn.replace("mysql+aiomysql://", "mysql://", 1))
            query = parse_qs(parsed.query)
            self._pool = await aiomysql.create_pool(
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port or 3306,
                user=parsed.username or "",
                password=parsed.password or "",
                db=(parsed.path or "/").lstrip("/") or None,
                minsize=1,
                maxsize=5,
                autocommit=True,
                charset=query.get("charset", ["utf8mb4"])[0],
            )
        return self._pool

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
        pool = await self._connect()
        collection = SQLSchemaCollection(
            db_type=self.db_type,
            database_id=database_id,
            collection_job_id=collection_job_id,
        )
        async with pool.acquire() as connection:
            async with connection.cursor() as cursor:
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
                await cursor.execute(
                    "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                )
                await cursor.execute(
                    f"SET SESSION MAX_EXECUTION_TIME={timeout_milliseconds}"
                )
                await cursor.execute(
                    "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"
                )
                try:
                    if table_schemas is None:
                        await cursor.execute(
                            """
                            SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_COMMENT,
                                   TABLE_ROWS
                            FROM information_schema.TABLES
                            WHERE TABLE_SCHEMA = %s
                              AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
                            ORDER BY TABLE_SCHEMA, TABLE_NAME
                            """,
                            (self.database,),
                        )
                        tables = await cursor.fetchall()
                    else:
                        tables = [
                            (
                                table.raw_schema_name,
                                table.raw_table_name,
                                table.raw_table_label,
                                table.row_count,
                                table,
                            )
                            for table in table_schemas
                        ]
                    for (
                        database_name,
                        table_name,
                        table_comment,
                        row_count,
                        *existing,
                    ) in tables:
                        schema_name = str(database_name)
                        table_name = str(table_name)
                        if is_excluded(
                            schema_name,
                            table_name,
                            exclude_schemas=exclude_schemas,
                            exclude_tables=exclude_tables,
                        ):
                            continue
                        table = (
                            existing[0]
                            if existing
                            else await self._collect_table(
                                connection,
                                schema_name,
                                table_name,
                                str(table_comment or ""),
                                int(row_count or 0),
                                sample_rows=sample_rows,
                            )
                        )
                        table = table.model_copy(
                            update={"database_id": database_id}
                        )
                        collection.tables.append(table)
                        if not existing:
                            collection.graph_edges.extend(
                                graph_edges_from_foreign_keys(
                                    table,
                                    allow_real_table_names=(
                                        self.allow_real_table_names
                                    ),
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
                            persist_field_value_page=(
                                persist_field_value_page
                            ),
                            annotate_field_value_page=(
                                annotate_field_value_page
                            ),
                            control_callback=control_callback,
                        )
                        collection.collected_field_values.extend(values)
                        collection.field_statistics.extend(statistics)
                except BaseException:
                    await connection.rollback()
                    raise
                else:
                    await connection.commit()
        return collection

    async def _collect_table(
        self,
        connection,
        schema_name: str,
        table_name: str,
        table_comment: str,
        row_count: int,
        *,
        sample_rows: int,
    ):
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT,
                       ORDINAL_POSITION, COLUMN_KEY, COLUMN_COMMENT
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                ORDER BY ORDINAL_POSITION
                """,
                (schema_name, table_name),
            )
            column_rows = await cursor.fetchall()
            columns = [
                SQLColumnInfo(
                    name=str(row[0]),
                    random_name="",
                    data_type=str(row[1] or ""),
                    nullable=str(row[2]).upper() == "YES",
                    default=str(row[3]) if row[3] is not None else None,
                    primary_key=str(row[5]).upper() == "PRI",
                    ordinal_position=int(row[4]),
                    comment=str(row[6] or ""),
                )
                for row in column_rows
            ]
            await cursor.execute(
                """
                SELECT CONSTRAINT_NAME, COLUMN_NAME,
                       REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME,
                       REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                  AND REFERENCED_TABLE_NAME IS NOT NULL
                ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION
                """,
                (schema_name, table_name),
            )
            foreign_rows = await cursor.fetchall()
            foreign_keys = [
                SQLForeignKeyInfo(
                    column_name=str(row[1]),
                    referred_schema=str(row[2]),
                    referred_table=str(row[3]),
                    referred_column=str(row[4]),
                    constraint_name=str(row[0]),
                    cardinality="many_to_one",
                )
                for row in foreign_rows
            ]
            await cursor.execute(
                """
                SELECT INDEX_NAME, NON_UNIQUE, INDEX_TYPE, COLUMN_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                ORDER BY INDEX_NAME, SEQ_IN_INDEX
                """,
                (schema_name, table_name),
            )
            index_rows = await cursor.fetchall()
            grouped: dict[str, SQLIndexInfo] = {}
            for row in index_rows:
                index = grouped.setdefault(
                    str(row[0]),
                    SQLIndexInfo(
                        name=str(row[0]),
                        unique=not bool(row[1]),
                        index_type=str(row[2] or ""),
                    ),
                )
                index.columns.append(str(row[3]))
            await cursor.execute(
                f"SELECT * FROM "
                f"{quote_identifier(schema_name, dialect='mysql')}."
                f"{quote_identifier(table_name, dialect='mysql')} LIMIT %s",
                (max(0, int(sample_rows)),),
            )
            sample_rows_data = await cursor.fetchall()
            sample_columns = [str(item[0]) for item in cursor.description or []]
        # SHOW CREATE TABLE 返回数据库保存的原始 DDL，包含表注释、列注释、
        # 索引、引擎、字符集和其他表级选项。不得用 information_schema
        # 字段重新拼接替代，否则会丢失原始定义内容。
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SHOW CREATE TABLE "
                f"{quote_identifier(schema_name, dialect='mysql')}."
                f"{quote_identifier(table_name, dialect='mysql')}"
            )
            create_row = await cursor.fetchone()
        ddl = (
            str(create_row[1])
            if create_row and len(create_row) > 1 and create_row[1]
            else self._mysql_ddl(schema_name, table_name, columns, foreign_keys)
        )
        sample_data = [
            dict(zip(sample_columns, row, strict=False)) for row in sample_rows_data
        ]
        return schema_from_parts(
            db_type=self.db_type,
            schema_name=schema_name,
            table_name=table_name,
            ddl=ddl,
            columns=columns,
            foreign_keys=foreign_keys,
            indexes=list(grouped.values()),
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
        table_ident = quote_identifier(table.raw_table_name, dialect="mysql")
        async with connection.cursor() as cursor:
            for column_name in selected:
                column = known.get(str(column_name))
                if column is None:
                    continue
                column_ident = quote_identifier(column.name, dialect="mysql")
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
                    await cursor.execute(
                        f"""
                        SELECT COUNT(*) AS total_count,
                               COUNT({column_ident}) AS non_null_count,
                               COUNT(DISTINCT {column_ident}) AS distinct_count
                        FROM {table_ident}
                        """
                    )
                    statistics_row = await cursor.fetchone()
                    await cursor.execute(
                        f"""
                        SELECT CAST({column_ident} AS CHAR) AS value
                        FROM {table_ident}
                        WHERE {column_ident} IS NOT NULL
                        ORDER BY CAST({column_ident} AS CHAR)
                        LIMIT %s
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
                    )
                    sample_values = [
                        str(row[0]) for row in await cursor.fetchall()
                    ]
                    frequencies: dict[str, int] = {}
                    for item in sample_values:
                        category = classify_field_value(item)
                        frequencies[category] = (
                            frequencies.get(category, 0) + 1
                        )
                    denominator = max(1, len(sample_values))
                    statistics = calculate_field_index_statistics(
                        collection_job_id=collection_job_id,
                        database_id=database_id,
                        db_type=self.db_type,
                        schema_name=table.raw_schema_name,
                        table_name=table.raw_table_name,
                        column=column,
                        row_count=table.row_count,
                        non_null_count=int(statistics_row[1] or 0),
                        distinct_count=int(statistics_row[2] or 0),
                        lengths=[len(item) for item in sample_values],
                        pattern_distribution={
                            key: value / denominator
                            for key, value in frequencies.items()
                        },
                        threshold=float(
                            getattr(
                                config,
                                "field_score_threshold",
                                75.0,
                            )
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
                        await cursor.execute(
                            f"""
                            SELECT CAST({column_ident} AS CHAR) AS value,
                                   COUNT(*) AS value_count
                            FROM {table_ident}
                            WHERE {column_ident} IS NOT NULL
                            GROUP BY CAST({column_ident} AS CHAR)
                            ORDER BY CAST({column_ident} AS CHAR)
                            LIMIT %s
                            """,
                            (fetch_size,),
                        )
                    else:
                        await cursor.execute(
                            f"""
                            SELECT CAST({column_ident} AS CHAR) AS value,
                                   COUNT(*) AS value_count
                            FROM {table_ident}
                            WHERE {column_ident} IS NOT NULL
                              AND CAST({column_ident} AS CHAR) > %s
                            GROUP BY CAST({column_ident} AS CHAR)
                            ORDER BY CAST({column_ident} AS CHAR)
                            LIMIT %s
                            """,
                            (cursor_text, fetch_size),
                        )
                    page_rows = await cursor.fetchall()
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
                            raw_value=row[0],
                            value_count=int(row[1] or 0),
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
                        if item.annotation is None
                        or item.annotation.should_index
                    )
                    processed_values += len(page_rows)
                    cursor_text = str(page_rows[-1][0])
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

    @staticmethod
    def _mysql_ddl(schema_name, table_name, columns, foreign_keys) -> str:
        lines = [
            f"CREATE TABLE {quote_identifier(schema_name, dialect='mysql')}."
            f"{quote_identifier(table_name, dialect='mysql')} ("
        ]
        for column in columns:
            nullable = "" if column.nullable else " NOT NULL"
            default = f" DEFAULT {column.default}" if column.default else ""
            lines.append(
                f"  {quote_identifier(column.name, dialect='mysql')} "
                f"{column.data_type}{nullable}{default},"
            )
        for foreign_key in foreign_keys:
            lines.append(
                f"  FOREIGN KEY ({quote_identifier(foreign_key.column_name, dialect='mysql')}) "
                f"REFERENCES {quote_identifier(foreign_key.referred_schema, dialect='mysql')}."
                f"{quote_identifier(foreign_key.referred_table, dialect='mysql')}("
                f"{quote_identifier(foreign_key.referred_column, dialect='mysql')}),"
            )
        if lines[-1].endswith(","):
            lines[-1] = lines[-1][:-1]
        lines.append(");")
        return "\n".join(lines)

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
