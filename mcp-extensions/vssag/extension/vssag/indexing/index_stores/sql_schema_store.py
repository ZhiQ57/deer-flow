"""Persistent SQL Schema index store.

SQLite is the local backend and reference implementation.  PostgreSQL uses the
same logical tables and is provided by :mod:`postgres_sql_schema_store`.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
from collections import defaultdict, deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from pydantic import BaseModel

from ...vssag_schemas.sql import (
    SQLEvidence,
    SQLEvidenceCandidate,
    SQLCollectedFieldValue,
    SQLFieldIndexStatistics,
    SQLFieldValue,
    SQLFieldValueAnnotation,
    SQLFieldValueCDCUpdate,
    SQLFieldValueCollectionCheckpoint,
    SQLFieldValueCollectionJob,
    SQLFeedback,
    SQLGraphEdge,
    SQLSchemaCollection,
    SQLFieldValueStreamMessage,
    SQLTableSchema,
)
from ...vssag_schemas.sql.scope import SQLScope


class SQLSchemaStore:
    """SQLite-backed SQL Schema index store."""

    backend = "sqlite"

    def __init__(
        self,
        dsn: str = ":memory:",
        *,
        namespace: str = "sql_schema",
        embedding_dimension: int = 0,
        embedding_model: str = "",
    ):
        self.dsn = dsn or ":memory:"
        self.namespace = namespace
        self.embedding_dimension = max(0, int(embedding_dimension))
        self.embedding_model = embedding_model
        self.path = self._parse_path(self.dsn)
        self._memory_connection: sqlite3.Connection | None = None
        self._fts_enabled = False
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        else:
            self._memory_connection = sqlite3.connect(
                ":memory:",
                check_same_thread=False,
            )
            self._memory_connection.row_factory = sqlite3.Row
            self._memory_connection.execute("PRAGMA foreign_keys = ON")
        self._initialize()

    @staticmethod
    def _parse_path(dsn: str) -> str:
        if dsn.startswith("sqlite:///"):
            path = unquote(urlparse(dsn).path)
            if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
                path = path[1:]
            return path or ":memory:"
        if dsn.startswith("sqlite://"):
            return unquote(dsn[len("sqlite://") :]) or ":memory:"
        return dsn

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS nl2sql_table_indexs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    database_id INTEGER NOT NULL DEFAULT 0,
                    db_type TEXT NOT NULL,
                    raw_schema_name TEXT NOT NULL,
                    raw_table_name TEXT NOT NULL,
                    random_table_name TEXT NOT NULL,
                    raw_table_label TEXT,
                    raw_ddl_text TEXT NOT NULL,
                    raw_ddl_hash TEXT NOT NULL,
                    random_ddl_text TEXT NOT NULL,
                    raw_schema_summary TEXT NOT NULL,
                    random_schema_summary TEXT NOT NULL,
                    columns_json TEXT NOT NULL,
                    foreign_keys_json TEXT NOT NULL,
                    indexes_json TEXT NOT NULL,
                    row_count INTEGER,
                    sample_rows_json TEXT NOT NULL,
                    qa_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        database_id, db_type, raw_schema_name, raw_table_name
                    )
                );

                CREATE TABLE IF NOT EXISTS nl2sql_field_value_indexs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    database_id INTEGER NOT NULL,
                    schema_name TEXT NOT NULL,
                    raw_value TEXT NOT NULL,
                    raw_table_name TEXT NOT NULL,
                    random_table_name TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    aliases TEXT NOT NULL,
                    retrieval_text TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        database_id, schema_name, raw_table_name,
                        column_name, raw_value
                    )
                );

                CREATE TABLE IF NOT EXISTS nl2sql_field_value_collection_jobs (
                    job_id TEXT PRIMARY KEY,
                    database_id INTEGER NOT NULL,
                    db_type TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    processed_tables INTEGER NOT NULL DEFAULT 0,
                    processed_fields INTEGER NOT NULL DEFAULT 0,
                    processed_values INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT '',
                    cdc_start_position TEXT NOT NULL DEFAULT '',
                    cdc_end_position TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS nl2sql_field_index_statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_job_id TEXT NOT NULL,
                    database_id INTEGER NOT NULL,
                    schema_name TEXT NOT NULL,
                    raw_table_name TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    statistics_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (collection_job_id)
                        REFERENCES nl2sql_field_value_collection_jobs(job_id)
                        ON DELETE CASCADE,
                    UNIQUE (
                        collection_job_id, database_id,
                        schema_name, raw_table_name, column_name
                    )
                );

                CREATE TABLE IF NOT EXISTS nl2sql_field_value_collection_checkpoints (
                    collection_job_id TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    raw_table_name TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    last_raw_value TEXT NOT NULL DEFAULT '',
                    processed_values INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'running',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (collection_job_id)
                        REFERENCES nl2sql_field_value_collection_jobs(job_id)
                        ON DELETE CASCADE,
                    PRIMARY KEY (
                        collection_job_id, schema_name,
                        raw_table_name, column_name
                    )
                );

                CREATE TABLE IF NOT EXISTS nl2sql_field_value_runtime_states (
                    value_index_id INTEGER PRIMARY KEY,
                    value_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    tombstone_at TEXT,
                    collection_job_id TEXT NOT NULL,
                    FOREIGN KEY (value_index_id)
                        REFERENCES nl2sql_field_value_indexs(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (collection_job_id)
                        REFERENCES nl2sql_field_value_collection_jobs(job_id)
                        ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS nl2sql_field_value_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    value_index_id INTEGER,
                    collection_job_id TEXT NOT NULL,
                    database_id INTEGER NOT NULL,
                    schema_name TEXT NOT NULL,
                    raw_table_name TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    raw_value TEXT NOT NULL,
                    should_index INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    aliases TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (value_index_id)
                        REFERENCES nl2sql_field_value_indexs(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (collection_job_id)
                        REFERENCES nl2sql_field_value_collection_jobs(job_id)
                        ON DELETE CASCADE,
                    UNIQUE (collection_job_id, input_hash)
                );

                CREATE INDEX IF NOT EXISTS
                    idx_field_value_annotation_cache
                    ON nl2sql_field_value_annotations(
                        input_hash, model_name, prompt_version, updated_at
                    );

                CREATE TABLE IF NOT EXISTS
                nl2sql_field_value_cdc_source_positions (
                    database_id INTEGER NOT NULL,
                    db_type TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_position TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (database_id, db_type, source_name)
                );

                CREATE TABLE IF NOT EXISTS nl2sql_field_value_cdc_events (
                    event_id TEXT PRIMARY KEY,
                    database_id INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    source_position TEXT NOT NULL,
                    source_order INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    transport_name TEXT NOT NULL,
                    transport_partition TEXT NOT NULL,
                    transport_position TEXT NOT NULL,
                    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS
                nl2sql_field_value_cdc_field_positions (
                    database_id INTEGER NOT NULL,
                    schema_name TEXT NOT NULL,
                    raw_table_name TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    source_position TEXT NOT NULL,
                    source_order INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (
                        database_id, schema_name,
                        raw_table_name, column_name
                    )
                );

                CREATE TABLE IF NOT EXISTS
                nl2sql_field_value_cdc_transport_positions (
                    database_id INTEGER NOT NULL,
                    transport_name TEXT NOT NULL,
                    transport_partition TEXT NOT NULL,
                    transport_position TEXT NOT NULL,
                    source_position TEXT NOT NULL,
                    source_order INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (
                        database_id, transport_name, transport_partition
                    )
                );

                CREATE TABLE IF NOT EXISTS nl2sql_evidence_indexs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    triggers_json TEXT NOT NULL,
                    retrieval_text TEXT NOT NULL,
                    evidence_content TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS nl2sql_graph_indexs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    db_type TEXT NOT NULL,
                    raw_schema_name TEXT NOT NULL,
                    related_raw_schema_name TEXT NOT NULL,
                    raw_table_name TEXT NOT NULL,
                    random_table_name TEXT NOT NULL,
                    raw_column_name TEXT,
                    random_column_name TEXT,
                    related_raw_table_name TEXT NOT NULL,
                    related_random_table_name TEXT NOT NULL,
                    related_raw_column_name TEXT,
                    related_random_column_name TEXT,
                    relation_type TEXT NOT NULL,
                    relation_direction TEXT NOT NULL DEFAULT 'bidirectional',
                    cardinality TEXT NOT NULL DEFAULT 'unknown',
                    join_condition TEXT NOT NULL DEFAULT '',
                    evidence_source TEXT NOT NULL,
                    evidence_content TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        db_type, raw_schema_name, raw_table_name, raw_column_name,
                        related_raw_schema_name, related_raw_table_name,
                        related_raw_column_name, relation_type
                    )
                );

                CREATE TABLE IF NOT EXISTS nl2sql_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_query TEXT NOT NULL,
                    generated_sql TEXT NOT NULL,
                    execution_result TEXT NOT NULL,
                    used_tables_json TEXT NOT NULL,
                    join_conditions_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS nl2sql_evidence_candidate (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_query TEXT NOT NULL,
                    candidate_trigger TEXT NOT NULL,
                    candidate_evidence TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_nl2sql_table_search
                    ON nl2sql_table_indexs(raw_schema_summary, random_schema_summary);
                CREATE INDEX IF NOT EXISTS idx_nl2sql_value_search
                    ON nl2sql_field_value_indexs(retrieval_text);
                CREATE INDEX IF NOT EXISTS idx_nl2sql_evidence_search
                    ON nl2sql_evidence_indexs(retrieval_text);
                CREATE INDEX IF NOT EXISTS idx_nl2sql_graph_source
                    ON nl2sql_graph_indexs(raw_schema_name, raw_table_name, status);
                CREATE INDEX IF NOT EXISTS idx_nl2sql_graph_target
                    ON nl2sql_graph_indexs(related_raw_schema_name, related_raw_table_name, status);
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_nl2sql_value_identity
                    ON nl2sql_field_value_indexs(
                        database_id, schema_name, raw_table_name, column_name
                    )
                """
            )
            if self.embedding_dimension:
                suffix = self.embedding_dimension
                for table_name, parent_table, parent_key in (
                    (
                        f"nl2sql_table_embeddings_{suffix}",
                        "nl2sql_table_indexs",
                        "table_index_id",
                    ),
                    (
                        f"nl2sql_field_value_embeddings_{suffix}",
                        "nl2sql_field_value_indexs",
                        "value_index_id",
                    ),
                    (
                        f"nl2sql_evidence_embeddings_{suffix}",
                        "nl2sql_evidence_indexs",
                        "evidence_index_id",
                    ),
                ):
                    safe_table = _safe_identifier(table_name)
                    retrieval_hash_column = (
                        "retrieval_text_hash TEXT NOT NULL DEFAULT '',"
                        if parent_key == "value_index_id"
                        else ""
                    )
                    connection.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {safe_table} (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            {parent_key} INTEGER NOT NULL,
                            model_name TEXT NOT NULL,
                            {retrieval_hash_column}
                            embedding_json TEXT NOT NULL,
                            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE ({parent_key}, model_name)
                        )
                        """
                    )
                    if parent_key == "value_index_id":
                        columns = {
                            str(row["name"])
                            for row in connection.execute(
                                f"PRAGMA table_info({safe_table})"
                            ).fetchall()
                        }
                        if "retrieval_text_hash" not in columns:
                            connection.execute(
                                f"""
                                ALTER TABLE {safe_table}
                                ADD COLUMN retrieval_text_hash TEXT
                                NOT NULL DEFAULT ''
                                """
                            )
                    connection.execute(
                        f"""
                        CREATE INDEX IF NOT EXISTS idx_{safe_table}_{parent_key}
                        ON {safe_table}({parent_key})
                        """
                    )
            try:
                connection.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS nl2sql_table_fts
                    USING fts5(row_id UNINDEXED, retrieval_text, tokenize='trigram');
                    CREATE VIRTUAL TABLE IF NOT EXISTS nl2sql_value_fts
                    USING fts5(row_id UNINDEXED, retrieval_text, tokenize='trigram');
                    CREATE VIRTUAL TABLE IF NOT EXISTS nl2sql_evidence_fts
                    USING fts5(row_id UNINDEXED, retrieval_text, tokenize='trigram');
                    """
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                try:
                    connection.executescript(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS nl2sql_table_fts
                        USING fts5(row_id UNINDEXED, retrieval_text);
                        CREATE VIRTUAL TABLE IF NOT EXISTS nl2sql_value_fts
                        USING fts5(row_id UNINDEXED, retrieval_text);
                        CREATE VIRTUAL TABLE IF NOT EXISTS nl2sql_evidence_fts
                        USING fts5(row_id UNINDEXED, retrieval_text);
                        """
                    )
                    self._fts_enabled = True
                except sqlite3.OperationalError:
                    self._fts_enabled = False
            if self._fts_enabled:
                counts = [
                    connection.execute(
                        "SELECT COUNT(*) FROM nl2sql_table_fts"
                    ).fetchone()[0],
                    connection.execute(
                        "SELECT COUNT(*) FROM nl2sql_value_fts"
                    ).fetchone()[0],
                    connection.execute(
                        "SELECT COUNT(*) FROM nl2sql_evidence_fts"
                    ).fetchone()[0],
                ]
                base_counts = [
                    connection.execute(
                        "SELECT COUNT(*) FROM nl2sql_table_indexs"
                    ).fetchone()[0],
                    connection.execute(
                        "SELECT COUNT(*) FROM nl2sql_field_value_indexs"
                    ).fetchone()[0],
                    connection.execute(
                        "SELECT COUNT(*) FROM nl2sql_evidence_indexs"
                    ).fetchone()[0],
                ]
                if counts != base_counts:
                    self._rebuild_fts_sync(connection)

    async def initialize(self) -> None:
        """初始化 SQLite SQL Schema 与字段值索引表。

        Returns:
            无返回值。
        """

        await asyncio.to_thread(self._initialize)

    async def begin_collection_job(
        self,
        job: SQLFieldValueCollectionJob,
    ) -> None:
        """创建或恢复字段值采集任务。

        Args:
            job: 待创建或恢复的字段值采集任务。
        """

        await asyncio.to_thread(self._begin_collection_job_sync, job)

    def _begin_collection_job_sync(
        self,
        job: SQLFieldValueCollectionJob,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO nl2sql_field_value_collection_jobs(
                    job_id, database_id, db_type, mode, status, started_at,
                    cdc_start_position, cdc_end_position
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status,
                    started_at=excluded.started_at,
                    error_message='',
                    cdc_start_position=excluded.cdc_start_position,
                    cdc_end_position=excluded.cdc_end_position
                """,
                (
                    job.job_id,
                    job.database_id,
                    job.db_type,
                    job.mode,
                    job.status,
                    job.started_at,
                    job.cdc_start_position,
                    job.cdc_end_position,
                ),
            )

    async def complete_collection_job(
        self,
        job: SQLFieldValueCollectionJob,
    ) -> None:
        """提交字段值采集任务状态和统计信息。

        Args:
            job: 已完成、失败或取消的字段值采集任务。
        """

        await asyncio.to_thread(self._complete_collection_job_sync, job)

    def _complete_collection_job_sync(
        self,
        job: SQLFieldValueCollectionJob,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE nl2sql_field_value_collection_jobs
                SET status=?, finished_at=?, processed_tables=?,
                    processed_fields=?, processed_values=?,
                    error_count=?, error_message=?,
                    cdc_start_position=?, cdc_end_position=?
                WHERE job_id=?
                  AND (status <> 'cancelled' OR ?='cancelled')
                """,
                (
                    job.status,
                    job.finished_at,
                    job.processed_tables,
                    job.processed_fields,
                    job.processed_values,
                    job.error_count,
                    job.error_message,
                    job.cdc_start_position,
                    job.cdc_end_position,
                    job.job_id,
                    job.status,
                ),
            )

    async def cancel_collection_job(self, job_id: str) -> bool:
        """取消运行中或等待恢复的字段值采集任务。

        Args:
            job_id: 字段值采集任务 ID。

        Returns:
            成功取消返回真；任务不存在或已经结束返回假。
        """

        def cancel_job() -> bool:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE nl2sql_field_value_collection_jobs
                    SET status='cancelled',
                        finished_at=CURRENT_TIMESTAMP,
                        error_message='cancelled by request'
                    WHERE job_id=?
                      AND status IN ('running', 'failed', 'paused')
                    """,
                    (job_id,),
                )
                return cursor.rowcount > 0

        return await asyncio.to_thread(cancel_job)

    async def pause_collection_job(self, job_id: str) -> bool:
        """将字段值采集任务标记为可恢复的暂停状态。

        Args:
            job_id: 字段值采集任务 ID。

        Returns:
            任务存在且状态已更新时返回 True。
        """

        def pause_job() -> bool:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE nl2sql_field_value_collection_jobs
                    SET status='paused',
                        finished_at=CURRENT_TIMESTAMP,
                        error_message='paused by request'
                    WHERE job_id=?
                      AND status IN ('running', 'failed')
                    """,
                    (job_id,),
                )
                return cursor.rowcount > 0

        return await asyncio.to_thread(pause_job)

    async def is_collection_job_cancelled(self, job_id: str) -> bool:
        """检查字段值采集任务是否已取消。

        Args:
            job_id: 字段值采集任务 ID。

        Returns:
            任务状态为 cancelled 时返回真。
        """

        def read_status() -> bool:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT status
                    FROM nl2sql_field_value_collection_jobs
                    WHERE job_id=?
                    """,
                    (job_id,),
                ).fetchone()
            return bool(row and row["status"] == "cancelled")

        return await asyncio.to_thread(read_status)

    async def save_collection_checkpoint(
        self,
        checkpoint: SQLFieldValueCollectionCheckpoint,
    ) -> None:
        """保存单字段采集断点。

        Args:
            checkpoint: 当前字段的采集断点。
        """

        await asyncio.to_thread(
            self._save_collection_checkpoint_sync,
            checkpoint,
        )

    def _save_collection_checkpoint_sync(
        self,
        checkpoint: SQLFieldValueCollectionCheckpoint,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO nl2sql_field_value_collection_checkpoints(
                    collection_job_id, schema_name, raw_table_name, column_name,
                    last_raw_value, processed_values, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(
                    collection_job_id, schema_name, raw_table_name, column_name
                )
                DO UPDATE SET
                    last_raw_value=excluded.last_raw_value,
                    processed_values=excluded.processed_values,
                    status=excluded.status,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    checkpoint.collection_job_id,
                    checkpoint.schema_name,
                    checkpoint.raw_table_name,
                    checkpoint.column_name,
                    checkpoint.last_raw_value,
                    checkpoint.processed_values,
                    checkpoint.status,
                ),
            )

    async def save_collection_page(
        self,
        values: Sequence[SQLCollectedFieldValue],
        statistics: SQLFieldIndexStatistics,
        checkpoint: SQLFieldValueCollectionCheckpoint,
    ) -> None:
        """原子保存一页字段值、字段统计和断点。

        Args:
            values: 当前页通过安全规则的字段值。
            statistics: 当前字段的统计和选择结果。
            checkpoint: 当前页提交后的恢复位置。

        Raises:
            ValueError: 统计记录与断点不属于同一采集任务或字段。
        """

        await asyncio.to_thread(
            self._save_collection_page_sync,
            list(values),
            statistics,
            checkpoint,
        )

    def _save_collection_page_sync(
        self,
        values: list[SQLCollectedFieldValue],
        statistics: SQLFieldIndexStatistics,
        checkpoint: SQLFieldValueCollectionCheckpoint,
    ) -> None:
        if (
            statistics.collection_job_id != checkpoint.collection_job_id
            or statistics.schema_name != checkpoint.schema_name
            or statistics.raw_table_name != checkpoint.raw_table_name
            or statistics.column_name != checkpoint.column_name
        ):
            raise ValueError("字段值页的统计记录与 checkpoint 不一致")
        with self._connect() as connection:
            status = connection.execute(
                """
                SELECT status
                FROM nl2sql_field_value_collection_jobs
                WHERE job_id=?
                """,
                (checkpoint.collection_job_id,),
            ).fetchone()
            if status and status["status"] == "cancelled":
                raise asyncio.CancelledError("字段值采集任务已取消")
            connection.execute(
                """
                INSERT INTO nl2sql_field_index_statistics(
                    collection_job_id, database_id, schema_name,
                    raw_table_name, column_name, statistics_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    collection_job_id, database_id,
                    schema_name, raw_table_name, column_name
                )
                DO UPDATE SET statistics_json=excluded.statistics_json
                """,
                (
                    statistics.collection_job_id,
                    statistics.database_id,
                    statistics.schema_name,
                    statistics.raw_table_name,
                    statistics.column_name,
                    _json(statistics),
                ),
            )
            for collected in values:
                value = collected.value
                annotation = collected.annotation
                if annotation is None or annotation.should_index:
                    connection.execute(
                        """
                        INSERT INTO nl2sql_field_value_indexs(
                            database_id, schema_name, raw_value,
                            raw_table_name, random_table_name, column_name,
                            aliases, retrieval_text, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(
                            database_id, schema_name, raw_table_name,
                            column_name, raw_value
                        )
                        DO UPDATE SET
                            random_table_name=excluded.random_table_name,
                            aliases=excluded.aliases,
                            retrieval_text=excluded.retrieval_text,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            value.database_id,
                            value.schema_name,
                            value.raw_value,
                            value.raw_table_name,
                            value.random_table_name,
                            value.column_name,
                            _json(value.aliases),
                            value.retrieval_text or value.raw_value,
                        ),
                    )
                    row = connection.execute(
                        """
                        SELECT id
                        FROM nl2sql_field_value_indexs
                        WHERE database_id=? AND schema_name=?
                          AND raw_table_name=? AND column_name=? AND raw_value=?
                        """,
                        (
                            value.database_id,
                            value.schema_name,
                            value.raw_table_name,
                            value.column_name,
                            value.raw_value,
                        ),
                    ).fetchone()
                    value.id = int(row["id"])
                    connection.execute(
                        """
                        INSERT INTO nl2sql_field_value_runtime_states(
                            value_index_id, value_count, status,
                            first_seen_at, last_seen_at, tombstone_at,
                            collection_job_id
                        )
                        VALUES(
                            ?, ?, 'active', CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP, NULL, ?
                        )
                        ON CONFLICT(value_index_id) DO UPDATE SET
                            value_count=excluded.value_count,
                            status='active',
                            last_seen_at=CURRENT_TIMESTAMP,
                            tombstone_at=NULL,
                            collection_job_id=excluded.collection_job_id
                        """,
                        (
                            value.id,
                            collected.value_count,
                            checkpoint.collection_job_id,
                        ),
                    )
                if annotation is not None:
                    annotation.value_index_id = value.id
                    connection.execute(
                        """
                        INSERT INTO nl2sql_field_value_annotations(
                            value_index_id, collection_job_id, database_id,
                            schema_name, raw_table_name, column_name, raw_value,
                            should_index, confidence, aliases, reason,
                            model_name, prompt_version, input_hash,
                            review_status, updated_at
                        )
                        VALUES(
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT(collection_job_id, input_hash) DO UPDATE SET
                            value_index_id=excluded.value_index_id,
                            should_index=excluded.should_index,
                            confidence=excluded.confidence,
                            aliases=excluded.aliases,
                            reason=excluded.reason,
                            model_name=excluded.model_name,
                            prompt_version=excluded.prompt_version,
                            review_status=excluded.review_status,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            annotation.value_index_id,
                            annotation.collection_job_id,
                            annotation.database_id,
                            annotation.schema_name,
                            annotation.raw_table_name,
                            annotation.column_name,
                            annotation.raw_value,
                            int(annotation.should_index),
                            annotation.confidence,
                            _json(annotation.aliases),
                            annotation.reason,
                            annotation.model_name,
                            annotation.prompt_version,
                            annotation.input_hash,
                            annotation.review_status,
                        ),
                    )
            connection.execute(
                """
                INSERT INTO nl2sql_field_value_collection_checkpoints(
                    collection_job_id, schema_name, raw_table_name, column_name,
                    last_raw_value, processed_values, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(
                    collection_job_id, schema_name, raw_table_name, column_name
                )
                DO UPDATE SET
                    last_raw_value=excluded.last_raw_value,
                    processed_values=excluded.processed_values,
                    status=excluded.status,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    checkpoint.collection_job_id,
                    checkpoint.schema_name,
                    checkpoint.raw_table_name,
                    checkpoint.column_name,
                    checkpoint.last_raw_value,
                    checkpoint.processed_values,
                    checkpoint.status,
                ),
            )

    async def reconcile_collection_job(
        self,
        collection: SQLSchemaCollection,
    ) -> None:
        """将完整采集任务未再出现的值状态标记为 tombstone。

        Args:
            collection: 本次完整采集结果。
        """

        await asyncio.to_thread(self._reconcile_collection_job_sync, collection)

    def _reconcile_collection_job_sync(
        self,
        collection: SQLSchemaCollection,
    ) -> None:
        protected = {
            (
                statistics.schema_name,
                statistics.raw_table_name,
                statistics.column_name,
            )
            for statistics in collection.field_statistics
            if (
                statistics.values_truncated
                or statistics.selection_decision == "uncertain"
            )
        }
        scanned_fields = {
            (
                statistics.schema_name,
                statistics.raw_table_name,
                statistics.column_name,
            )
            for statistics in collection.field_statistics
        }
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT value.id, value.schema_name,
                       value.raw_table_name, value.column_name
                FROM nl2sql_field_value_indexs AS value
                JOIN nl2sql_field_value_runtime_states AS state
                  ON state.value_index_id=value.id
                WHERE value.database_id=?
                  AND state.status='active'
                  AND state.collection_job_id<>?
                """,
                (
                    collection.database_id,
                    collection.collection_job_id,
                ),
            ).fetchall()
            for row in rows:
                key = (
                    str(row["schema_name"]),
                    str(row["raw_table_name"]),
                    str(row["column_name"]),
                )
                if key in protected:
                    continue
                if key not in scanned_fields:
                    continue
                connection.execute(
                    """
                    UPDATE nl2sql_field_value_runtime_states
                    SET status='tombstone',
                        tombstone_at=CURRENT_TIMESTAMP
                    WHERE value_index_id=?
                    """,
                    (int(row["id"]),),
                )

    async def find_resumable_collection_job(
        self,
        *,
        database_id: int,
        db_type: str,
        mode: str,
    ) -> SQLFieldValueCollectionJob | None:
        """查找最近一次可恢复的字段值采集任务。

        Args:
            database_id: 目标数据库 ID。
            db_type: 目标数据库类型。
            mode: manual 或 auto。

        Returns:
            最近的运行中或失败任务；不存在时返回空值。
        """

        def read_job() -> SQLFieldValueCollectionJob | None:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM nl2sql_field_value_collection_jobs
                    WHERE database_id=? AND db_type=? AND mode=?
                      AND status IN ('running', 'failed', 'paused')
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    (database_id, db_type, mode),
                ).fetchone()
                if row is None:
                    return None
                return SQLFieldValueCollectionJob(
                    job_id=str(row["job_id"]),
                    database_id=int(row["database_id"]),
                    db_type=str(row["db_type"]),
                    mode=str(row["mode"]),
                    status=str(row["status"]),
                    started_at=str(row["started_at"]),
                    finished_at=row["finished_at"],
                    processed_tables=int(row["processed_tables"] or 0),
                    processed_fields=int(row["processed_fields"] or 0),
                    processed_values=int(row["processed_values"] or 0),
                    error_count=int(row["error_count"] or 0),
                    error_message=str(row["error_message"] or ""),
                    cdc_start_position=str(row["cdc_start_position"] or ""),
                    cdc_end_position=str(row["cdc_end_position"] or ""),
                )

        return await asyncio.to_thread(read_job)

    async def list_collection_checkpoints(
        self,
        collection_job_id: str,
    ) -> list[SQLFieldValueCollectionCheckpoint]:
        """读取字段值采集任务的全部断点。

        Args:
            collection_job_id: 字段值采集任务 ID。

        Returns:
            该任务已经保存的字段级断点。
        """

        def read_checkpoints() -> list[SQLFieldValueCollectionCheckpoint]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM nl2sql_field_value_collection_checkpoints
                    WHERE collection_job_id=?
                    ORDER BY schema_name, raw_table_name, column_name
                    """,
                    (collection_job_id,),
                ).fetchall()
                return [
                    SQLFieldValueCollectionCheckpoint(
                        collection_job_id=str(row["collection_job_id"]),
                        schema_name=str(row["schema_name"]),
                        raw_table_name=str(row["raw_table_name"]),
                        column_name=str(row["column_name"]),
                        last_raw_value=str(row["last_raw_value"] or ""),
                        processed_values=int(row["processed_values"] or 0),
                        status=str(row["status"]),
                    )
                    for row in rows
                ]

        return await asyncio.to_thread(read_checkpoints)

    async def list_collection_field_values(
        self,
        collection_job_id: str,
    ) -> list[SQLCollectedFieldValue]:
        """读取指定采集任务已经原子提交的全部有效字段值。

        Args:
            collection_job_id: 字段值采集任务 ID。

        Returns:
            该任务已保存的字段值及数据库引用数量。
        """

        def read_values() -> list[SQLCollectedFieldValue]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT value.*, state.value_count
                    FROM nl2sql_field_value_indexs AS value
                    JOIN nl2sql_field_value_runtime_states AS state
                      ON state.value_index_id=value.id
                    WHERE state.collection_job_id=?
                      AND state.status='active'
                    ORDER BY value.schema_name, value.raw_table_name,
                             value.column_name, value.raw_value
                    """,
                    (collection_job_id,),
                ).fetchall()
            return [
                SQLCollectedFieldValue(
                    value=_value_from_row(row),
                    value_count=int(row["value_count"] or 0),
                )
                for row in rows
            ]

        return await asyncio.to_thread(read_values)

    async def get_cached_field_value_annotations(
        self,
        input_hashes: Sequence[str],
        *,
        model_name: str,
        prompt_version: str,
    ) -> dict[str, SQLFieldValueAnnotation]:
        """读取相同模型和提示词版本的字段值标注缓存。

        Args:
            input_hashes: 字段上下文与原始值的稳定摘要。
            model_name: LLM 模型名称。
            prompt_version: 标注提示词版本。

        Returns:
            以输入摘要为键的最近标注结果。
        """

        if not input_hashes:
            return {}

        def read_annotations() -> dict[str, SQLFieldValueAnnotation]:
            placeholders = ",".join("?" for _ in input_hashes)
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    SELECT *
                    FROM nl2sql_field_value_annotations
                    WHERE input_hash IN ({placeholders})
                      AND model_name=?
                      AND prompt_version=?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (
                        *[str(value) for value in input_hashes],
                        model_name,
                        prompt_version,
                    ),
                ).fetchall()
            result: dict[str, SQLFieldValueAnnotation] = {}
            for row in rows:
                input_hash = str(row["input_hash"])
                if input_hash in result:
                    continue
                result[input_hash] = _annotation_from_row(row)
            return result

        return await asyncio.to_thread(read_annotations)

    async def list_field_value_annotations(
        self,
        *,
        status: str = "pending",
        limit: int = 100,
    ) -> list[SQLFieldValueAnnotation]:
        """列出等待人工审核或已经审核的字段值标注。

        Args:
            status: pending、approved、rejected 或 disabled。
            limit: 最大返回数量。

        Returns:
            按更新时间和 ID 排序的字段值标注。

        Raises:
            ValueError: 审核状态不受支持。
        """

        if status not in {"pending", "approved", "rejected", "disabled"}:
            raise ValueError("字段值标注审核状态无效")

        def read_annotations() -> list[SQLFieldValueAnnotation]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM nl2sql_field_value_annotations
                    WHERE review_status=?
                    ORDER BY updated_at, id
                    LIMIT ?
                    """,
                    (status, max(1, int(limit))),
                ).fetchall()
            return [_annotation_from_row(row) for row in rows]

        return await asyncio.to_thread(read_annotations)

    async def review_field_value_annotation(
        self,
        annotation_id: int,
        *,
        status: str,
        aliases: Sequence[str] | None = None,
        reason: str | None = None,
    ) -> SQLFieldValueAnnotation | None:
        """人工审核字段值标注，并由下次全量校准应用决定。

        Args:
            annotation_id: 标注记录 ID。
            status: approved、rejected 或 disabled。
            aliases: 审核后确认的别名；空值表示保留原别名。
            reason: 人工审核原因；空值表示保留原原因。

        Returns:
            更新后的标注；记录不存在时返回空值。

        Raises:
            ValueError: 审核状态不受支持。
        """

        if status not in {"approved", "rejected", "disabled"}:
            raise ValueError("字段值标注审核状态无效")
        cleaned_aliases = None
        if aliases is not None:
            cleaned_aliases = []
            for alias in aliases:
                text = " ".join(str(alias).split())[:100]
                if text and text not in cleaned_aliases:
                    cleaned_aliases.append(text)
                if len(cleaned_aliases) >= 10:
                    break

        def update_annotation() -> SQLFieldValueAnnotation | None:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM nl2sql_field_value_annotations
                    WHERE id=?
                    """,
                    (int(annotation_id),),
                ).fetchone()
                if row is None:
                    return None
                connection.execute(
                    """
                    UPDATE nl2sql_field_value_annotations
                    SET should_index=?,
                        aliases=?,
                        reason=?,
                        review_status=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        int(status == "approved"),
                        _json(
                            cleaned_aliases
                            if cleaned_aliases is not None
                            else json.loads(row["aliases"])
                        ),
                        str(reason)[:1000]
                        if reason is not None
                        else str(row["reason"]),
                        status,
                        int(annotation_id),
                    ),
                )
                updated = connection.execute(
                    """
                    SELECT *
                    FROM nl2sql_field_value_annotations
                    WHERE id=?
                    """,
                    (int(annotation_id),),
                ).fetchone()
            return _annotation_from_row(updated)

        return await asyncio.to_thread(update_annotation)

    async def get_cdc_source_position(
        self,
        *,
        database_id: int,
        db_type: str,
        source_name: str,
    ) -> str:
        """读取 CDC 源端已经安全发布的位置。

        Args:
            database_id: 目标数据库 ID。
            db_type: PostgreSQL 或 MySQL。
            source_name: 复制槽或 Binlog 读取器名称。

        Returns:
            已确认位置；首次运行时返回空字符串。
        """

        def read_position() -> str:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT source_position
                    FROM nl2sql_field_value_cdc_source_positions
                    WHERE database_id=? AND db_type=? AND source_name=?
                    """,
                    (database_id, db_type, source_name),
                ).fetchone()
            return str(row["source_position"]) if row else ""

        return await asyncio.to_thread(read_position)

    async def save_cdc_source_position(
        self,
        *,
        database_id: int,
        db_type: str,
        source_name: str,
        source_position: str,
    ) -> None:
        """保存已经成功发布到消息系统的 CDC 源位置。

        Args:
            database_id: 目标数据库 ID。
            db_type: PostgreSQL 或 MySQL。
            source_name: 复制槽或 Binlog 读取器名称。
            source_position: 已成功发布的位置。
        """

        def save_position() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO nl2sql_field_value_cdc_source_positions(
                        database_id, db_type, source_name,
                        source_position, updated_at
                    )
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(database_id, db_type, source_name)
                    DO UPDATE SET
                        source_position=excluded.source_position,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        database_id,
                        db_type,
                        source_name,
                        source_position,
                    ),
                )

        await asyncio.to_thread(save_position)

    async def save_collection_cdc_positions(
        self,
        *,
        collection_job_id: str,
        start_position: str,
        end_position: str,
    ) -> None:
        """保存全量采集任务的 CDC 起止位置。

        Args:
            collection_job_id: 字段值采集任务 ID。
            start_position: 全量扫描开始前的源位置。
            end_position: 扫描后增量回放完成的位置。
        """

        def save_positions() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE nl2sql_field_value_collection_jobs
                    SET cdc_start_position=?,
                        cdc_end_position=?
                    WHERE job_id=?
                    """,
                    (
                        start_position,
                        end_position,
                        collection_job_id,
                    ),
                )

        await asyncio.to_thread(save_positions)

    async def list_cdc_indexed_tables(
        self,
        database_id: int,
    ) -> list[tuple[str, str]]:
        """读取已完成采集任务确认需要索引的表。

        Args:
            database_id: 目标数据库 ID。

        Returns:
            去重后的 Schema 和原始表名。
        """

        def read_tables() -> list[tuple[str, str]]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT statistics.statistics_json
                    FROM nl2sql_field_index_statistics AS statistics
                    JOIN nl2sql_field_value_collection_jobs AS job
                      ON job.job_id=statistics.collection_job_id
                    WHERE statistics.database_id=?
                      AND job.status='completed'
                    ORDER BY statistics.id DESC
                    """,
                    (database_id,),
                ).fetchall()
            tables: set[tuple[str, str]] = set()
            for row in rows:
                statistics = SQLFieldIndexStatistics.model_validate_json(
                    row["statistics_json"]
                )
                if statistics.selection_decision in {
                    "manual",
                    "candidate",
                    "llm_reviewed",
                    "uncertain",
                }:
                    tables.add(
                        (
                            statistics.schema_name,
                            statistics.raw_table_name,
                        )
                    )
            return sorted(tables)

        return await asyncio.to_thread(read_tables)

    async def get_cdc_field_context(
        self,
        *,
        database_id: int,
        schema_name: str,
        raw_table_name: str,
        column_name: str,
    ) -> tuple[SQLFieldIndexStatistics, str] | None:
        """读取 CDC 字段最近一次成功统计和随机表名。

        Args:
            database_id: 目标数据库 ID。
            schema_name: 原始 Schema。
            raw_table_name: 原始表名。
            column_name: 原始字段名。

        Returns:
            字段统计和随机表名；未建立索引时返回空值。
        """

        def read_context():
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT statistics.statistics_json,
                           table_index.random_table_name
                    FROM nl2sql_field_index_statistics AS statistics
                    JOIN nl2sql_field_value_collection_jobs AS job
                      ON job.job_id=statistics.collection_job_id
                    JOIN nl2sql_table_indexs AS table_index
                      ON table_index.database_id=statistics.database_id
                     AND table_index.raw_schema_name=statistics.schema_name
                     AND table_index.raw_table_name=statistics.raw_table_name
                    WHERE statistics.database_id=?
                      AND statistics.schema_name=?
                      AND statistics.raw_table_name=?
                      AND statistics.column_name=?
                      AND job.status='completed'
                    ORDER BY statistics.id DESC
                    LIMIT 1
                    """,
                    (
                        database_id,
                        schema_name,
                        raw_table_name,
                        column_name,
                    ),
                ).fetchone()
            if row is None:
                return None
            return (
                SQLFieldIndexStatistics.model_validate_json(
                    row["statistics_json"]
                ),
                str(row["random_table_name"]),
            )

        return await asyncio.to_thread(read_context)

    async def apply_cdc_event(
        self,
        message: SQLFieldValueStreamMessage,
        updates: Sequence[SQLFieldValueCDCUpdate],
    ) -> str:
        """幂等应用一个 CDC 消息及其字段计数变化。

        Args:
            message: 带传输位点的统一 CDC 消息。
            updates: 已通过字段规则和 LLM 判断的字段更新。

        Returns:
            ``applied``、``ignored`` 或 ``duplicate``。
        """

        result = await asyncio.to_thread(
            self._apply_cdc_event_sync,
            message,
            list(updates),
        )
        if result == "applied" and self._fts_enabled:
            await asyncio.to_thread(self._rebuild_fts_sync)
        return result

    def _apply_cdc_event_sync(
        self,
        message: SQLFieldValueStreamMessage,
        updates: list[SQLFieldValueCDCUpdate],
    ) -> str:
        with self._connect() as connection:
            duplicate = connection.execute(
                """
                SELECT 1
                FROM nl2sql_field_value_cdc_events
                WHERE event_id=?
                """,
                (message.event.event_id,),
            ).fetchone()
            if duplicate:
                return "duplicate"
            applied = False
            for update in updates:
                if update.event_id != message.event.event_id:
                    raise ValueError("CDC 字段更新与消息 event_id 不一致")
                previous = connection.execute(
                    """
                    SELECT source_order
                    FROM nl2sql_field_value_cdc_field_positions
                    WHERE database_id=? AND schema_name=?
                      AND raw_table_name=? AND column_name=?
                    """,
                    (
                        update.database_id,
                        update.schema_name,
                        update.raw_table_name,
                        update.column_name,
                    ),
                ).fetchone()
                if (
                    previous is not None
                    and update.source_order < int(previous["source_order"])
                ):
                    continue
                if (
                    update.old_value is not None
                    and update.old_value != update.new_value
                ):
                    row = connection.execute(
                        """
                        SELECT value.id, state.value_count
                        FROM nl2sql_field_value_indexs AS value
                        JOIN nl2sql_field_value_runtime_states AS state
                          ON state.value_index_id=value.id
                        WHERE value.database_id=? AND value.schema_name=?
                          AND value.raw_table_name=? AND value.column_name=?
                          AND value.raw_value=?
                        """,
                        (
                            update.database_id,
                            update.schema_name,
                            update.raw_table_name,
                            update.column_name,
                            update.old_value,
                        ),
                    ).fetchone()
                    if row is not None:
                        next_count = max(
                            0,
                            int(row["value_count"] or 0) - 1,
                        )
                        connection.execute(
                            """
                            UPDATE nl2sql_field_value_runtime_states
                            SET value_count=?,
                                status=?,
                                last_seen_at=CURRENT_TIMESTAMP,
                                tombstone_at=CASE
                                  WHEN ?=0 THEN CURRENT_TIMESTAMP
                                  ELSE NULL
                                END
                            WHERE value_index_id=?
                            """,
                            (
                                next_count,
                                "tombstone"
                                if next_count == 0
                                else "active",
                                next_count,
                                int(row["id"]),
                            ),
                        )
                        applied = True
                value_index_id = None
                annotation = update.annotation
                if (
                    update.new_value is not None
                    and update.new_value != update.old_value
                    and (
                        annotation is None
                        or annotation.should_index
                    )
                ):
                    aliases = annotation.aliases if annotation else []
                    connection.execute(
                        """
                        INSERT INTO nl2sql_field_value_indexs(
                            database_id, schema_name, raw_value,
                            raw_table_name, random_table_name, column_name,
                            aliases, retrieval_text, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(
                            database_id, schema_name, raw_table_name,
                            column_name, raw_value
                        )
                        DO UPDATE SET
                            random_table_name=excluded.random_table_name,
                            aliases=excluded.aliases,
                            retrieval_text=excluded.retrieval_text,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            update.database_id,
                            update.schema_name,
                            update.new_value,
                            update.raw_table_name,
                            update.random_table_name,
                            update.column_name,
                            _json(aliases),
                            " ".join(
                                [update.new_value, *aliases]
                            ).strip(),
                        ),
                    )
                    row = connection.execute(
                        """
                        SELECT id
                        FROM nl2sql_field_value_indexs
                        WHERE database_id=? AND schema_name=?
                          AND raw_table_name=? AND column_name=?
                          AND raw_value=?
                        """,
                        (
                            update.database_id,
                            update.schema_name,
                            update.raw_table_name,
                            update.column_name,
                            update.new_value,
                        ),
                    ).fetchone()
                    value_index_id = int(row["id"])
                    connection.execute(
                        """
                        INSERT INTO nl2sql_field_value_runtime_states(
                            value_index_id, value_count, status,
                            first_seen_at, last_seen_at, tombstone_at,
                            collection_job_id
                        )
                        VALUES(
                            ?, 1, 'active', CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP, NULL, ?
                        )
                        ON CONFLICT(value_index_id) DO UPDATE SET
                            value_count=value_count + 1,
                            status='active',
                            last_seen_at=CURRENT_TIMESTAMP,
                            tombstone_at=NULL,
                            collection_job_id=excluded.collection_job_id
                        """,
                        (value_index_id, update.collection_job_id),
                    )
                    applied = True
                if annotation is not None:
                    annotation.value_index_id = value_index_id
                    connection.execute(
                        """
                        INSERT INTO nl2sql_field_value_annotations(
                            value_index_id, collection_job_id, database_id,
                            schema_name, raw_table_name, column_name, raw_value,
                            should_index, confidence, aliases, reason,
                            model_name, prompt_version, input_hash,
                            review_status, updated_at
                        )
                        VALUES(
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT(collection_job_id, input_hash)
                        DO UPDATE SET
                            value_index_id=excluded.value_index_id,
                            should_index=excluded.should_index,
                            confidence=excluded.confidence,
                            aliases=excluded.aliases,
                            reason=excluded.reason,
                            model_name=excluded.model_name,
                            prompt_version=excluded.prompt_version,
                            review_status=excluded.review_status,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            annotation.value_index_id,
                            annotation.collection_job_id,
                            annotation.database_id,
                            annotation.schema_name,
                            annotation.raw_table_name,
                            annotation.column_name,
                            annotation.raw_value,
                            int(annotation.should_index),
                            annotation.confidence,
                            _json(annotation.aliases),
                            annotation.reason,
                            annotation.model_name,
                            annotation.prompt_version,
                            annotation.input_hash,
                            annotation.review_status,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO nl2sql_field_value_cdc_field_positions(
                        database_id, schema_name, raw_table_name, column_name,
                        source_position, source_order, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(
                        database_id, schema_name, raw_table_name, column_name
                    )
                    DO UPDATE SET
                        source_position=excluded.source_position,
                        source_order=excluded.source_order,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        update.database_id,
                        update.schema_name,
                        update.raw_table_name,
                        update.column_name,
                        update.source_position,
                        update.source_order,
                    ),
                )
            status = "applied" if applied else "ignored"
            connection.execute(
                """
                INSERT INTO nl2sql_field_value_cdc_events(
                    event_id, database_id, event_json,
                    source_position, source_order, status, error_message,
                    transport_name, transport_partition,
                    transport_position, processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    message.event.event_id,
                    message.event.database_id,
                    message.event.model_dump_json(),
                    message.event.source_position,
                    message.event.source_order,
                    status,
                    message.transport_name,
                    message.transport_partition,
                    message.transport_position,
                ),
            )
            connection.execute(
                """
                INSERT INTO nl2sql_field_value_cdc_transport_positions(
                    database_id, transport_name, transport_partition,
                    transport_position, source_position,
                    source_order, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(
                    database_id, transport_name, transport_partition
                )
                DO UPDATE SET
                    transport_position=excluded.transport_position,
                    source_position=excluded.source_position,
                    source_order=excluded.source_order,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    message.event.database_id,
                    message.transport_name,
                    message.transport_partition,
                    message.transport_position,
                    message.event.source_position,
                    message.event.source_order,
                ),
            )
            return status

    async def upsert_collection(self, collection: SQLSchemaCollection) -> dict[str, int]:
        result = await asyncio.to_thread(self._upsert_collection_sync, collection)
        if self._fts_enabled:
            await asyncio.to_thread(self._rebuild_fts_sync)
        return result

    async def replace_collection(
        self,
        collection: SQLSchemaCollection,
        *,
        changed_tables: set[tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        """Replace the indexed tables for one database type atomically."""

        return await asyncio.to_thread(
            self._replace_collection_sync,
            collection,
            changed_tables,
        )

    async def sync_collection(
        self,
        collection: SQLSchemaCollection,
        *,
        changed_tables: set[tuple[str, str]],
        removed_tables: set[tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        """按 DDL Hash 差异同步集合，仅重建变化表及其依赖索引。

        Args:
            collection: 本次采集到的完整集合。
            changed_tables: DDL Hash 新增或变化的表。
            removed_tables: 已从数据源删除的表；为空时根据现有索引推导。

        Returns:
            本次实际写入的表 ID 映射。
        """

        # replace_collection 会同时处理当前集合之外的旧表；显式参数保留在
        # 公共接口中，便于 PostgreSQL/LanceDB 适配器使用同一调用约定。
        del removed_tables
        return await self.replace_collection(
            collection,
            changed_tables=set(changed_tables),
        )

    def _replace_collection_sync(
        self,
        collection: SQLSchemaCollection,
        changed_tables: set[tuple[str, str]] | None,
    ) -> dict[str, int]:
        keep = {(table.raw_schema_name, table.raw_table_name) for table in collection.tables}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, raw_schema_name, raw_table_name
                FROM nl2sql_table_indexs
                WHERE database_id=? AND db_type=?
                """,
                (collection.database_id, collection.db_type),
            ).fetchall()
            existing = {
                (str(row["raw_schema_name"]), str(row["raw_table_name"])): int(row["id"])
                for row in rows
            }
            changed = (
                set(keep)
                if changed_tables is None
                else set(changed_tables).intersection(keep)
            )
            stale = set(existing).difference(keep)
            affected = changed | stale
            current_columns = {
                (table.raw_schema_name, table.raw_table_name): {
                    column.name for column in table.columns
                }
                for table in collection.tables
            }
            for schema_name, table_name in affected:
                connection.execute(
                    """
                    DELETE FROM nl2sql_graph_indexs
                    WHERE db_type=?
                      AND (
                        (raw_schema_name=? AND raw_table_name=?)
                        OR (related_raw_schema_name=? AND related_raw_table_name=?)
                      )
                      AND (
                        relation_type='physical'
                        OR (relation_type='candidate' AND evidence_source='model_inference')
                      )
                    """,
                    (
                        collection.db_type,
                        schema_name,
                        table_name,
                        schema_name,
                        table_name,
                    ),
                )
                columns = current_columns.get((schema_name, table_name))
                value_sql = """
                    SELECT id
                    FROM nl2sql_field_value_indexs
                    WHERE database_id=? AND schema_name=?
                      AND raw_table_name=?
                """
                value_args: list[Any] = [
                    collection.database_id,
                    schema_name,
                    table_name,
                ]
                if columns:
                    placeholders = ",".join("?" for _ in columns)
                    value_sql += (
                        f" AND column_name NOT IN ({placeholders})"
                    )
                    value_args.extend(sorted(columns))
                value_ids = [
                    int(row["id"])
                    for row in connection.execute(
                        value_sql,
                        value_args,
                    ).fetchall()
                ]
                if self.embedding_dimension and value_ids:
                    placeholders = ",".join("?" for _ in value_ids)
                    connection.execute(
                        f"""
                        DELETE FROM nl2sql_field_value_embeddings_{self.embedding_dimension}
                        WHERE value_index_id IN ({placeholders})
                        """,
                        value_ids,
                    )
                if self._fts_enabled and value_ids:
                    placeholders = ",".join("?" for _ in value_ids)
                    connection.execute(
                        f"DELETE FROM nl2sql_value_fts WHERE row_id IN ({placeholders})",
                        value_ids,
                    )
                if value_ids:
                    placeholders = ",".join("?" for _ in value_ids)
                    connection.execute(
                        f"""
                        DELETE FROM nl2sql_field_value_indexs
                        WHERE id IN ({placeholders})
                        """,
                        value_ids,
                    )
                table_id = existing.get((schema_name, table_name))
                if table_id is None:
                    continue
                if self.embedding_dimension:
                    connection.execute(
                        f"DELETE FROM nl2sql_table_embeddings_{self.embedding_dimension} "
                        "WHERE table_index_id=?",
                        (table_id,),
                    )
                connection.execute(
                    "DELETE FROM nl2sql_table_indexs WHERE id=?",
                    (table_id,),
                )
                if self._fts_enabled:
                    connection.execute(
                        "DELETE FROM nl2sql_table_fts WHERE row_id=?",
                        (table_id,),
                    )
            delta = collection.model_copy(
                update={
                    "tables": [
                        table
                        for table in collection.tables
                        if (table.raw_schema_name, table.raw_table_name) in changed
                    ],
                    # 字段值属于采集任务一致性域，即使表结构未变化，也必须
                    # 更新运行状态，避免被完整校准误标为 tombstone。
                    "collected_field_values": list(
                        collection.collected_field_values
                    ),
                    "field_statistics": list(collection.field_statistics),
                    "graph_edges": [
                        edge
                        for edge in collection.graph_edges
                        if (
                            changed_tables is None
                            or (
                                (edge.raw_schema_name, edge.raw_table_name) in changed
                                or (
                                    edge.related_raw_schema_name,
                                    edge.related_raw_table_name,
                                )
                                in changed
                            )
                        )
                    ],
                }
            )
        result = self._upsert_collection_sync(delta)
        if self._fts_enabled:
            self._rebuild_fts_sync()
        return result

    def _upsert_collection_sync(self, collection: SQLSchemaCollection) -> dict[str, int]:
        table_ids: dict[str, int] = {}
        with self._connect() as connection:
            for table in collection.tables:
                row = connection.execute(
                    """
                    INSERT INTO nl2sql_table_indexs(
                        database_id, db_type, raw_schema_name, raw_table_name,
                        random_table_name,
                        raw_table_label, raw_ddl_text, raw_ddl_hash,
                        random_ddl_text, raw_schema_summary, random_schema_summary,
                        columns_json, foreign_keys_json, indexes_json, row_count,
                        sample_rows_json, qa_text, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            CURRENT_TIMESTAMP)
                    ON CONFLICT(
                        database_id, db_type, raw_schema_name, raw_table_name
                    )
                    DO UPDATE SET
                        random_table_name=excluded.random_table_name,
                        raw_table_label=excluded.raw_table_label,
                        raw_ddl_text=excluded.raw_ddl_text,
                        raw_ddl_hash=excluded.raw_ddl_hash,
                        random_ddl_text=excluded.random_ddl_text,
                        raw_schema_summary=excluded.raw_schema_summary,
                        random_schema_summary=excluded.random_schema_summary,
                        columns_json=excluded.columns_json,
                        foreign_keys_json=excluded.foreign_keys_json,
                        indexes_json=excluded.indexes_json,
                        row_count=excluded.row_count,
                        sample_rows_json=excluded.sample_rows_json,
                        qa_text=excluded.qa_text,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        table.database_id,
                        table.db_type,
                        table.raw_schema_name,
                        table.raw_table_name,
                        table.random_table_name,
                        table.raw_table_label,
                        table.raw_ddl_text,
                        table.raw_ddl_hash,
                        table.random_ddl_text,
                        table.raw_schema_summary,
                        table.random_schema_summary,
                        _json(table.columns),
                        _json(table.foreign_keys),
                        _json(table.indexes),
                        table.row_count,
                        _json(table.sample_rows),
                        table.qa_text,
                    ),
                )
                table_id = connection.execute(
                    """
                    SELECT id
                    FROM nl2sql_table_indexs
                    WHERE database_id=? AND db_type=?
                      AND raw_schema_name=? AND raw_table_name=?
                    """,
                    (
                        table.database_id,
                        table.db_type,
                        table.raw_schema_name,
                        table.raw_table_name,
                    ),
                ).fetchone()["id"]
                table_ids[f"{table.raw_schema_name}.{table.raw_table_name}"] = int(table_id)

            for collected in collection.collected_field_values:
                value = collected.value
                connection.execute(
                    """
                    INSERT INTO nl2sql_field_value_indexs(
                        database_id, schema_name, raw_value,
                        raw_table_name, random_table_name, column_name,
                        aliases, retrieval_text, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(
                        database_id, schema_name, raw_table_name,
                        column_name, raw_value
                    )
                    DO UPDATE SET
                        random_table_name=excluded.random_table_name,
                        aliases=excluded.aliases,
                        retrieval_text=excluded.retrieval_text,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        value.database_id,
                        value.schema_name,
                        value.raw_value,
                        value.raw_table_name,
                        value.random_table_name,
                        value.column_name,
                        _json(value.aliases),
                        value.retrieval_text or value.raw_value,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT id FROM nl2sql_field_value_indexs
                    WHERE database_id=? AND schema_name=?
                      AND raw_table_name=? AND column_name=? AND raw_value=?
                    """,
                    (
                        value.database_id,
                        value.schema_name,
                        value.raw_table_name,
                        value.column_name,
                        value.raw_value,
                    ),
                ).fetchone()
                value.id = int(row["id"]) if row else None
                if value.id is not None:
                    connection.execute(
                        """
                        INSERT INTO nl2sql_field_value_runtime_states(
                            value_index_id, value_count, status,
                            first_seen_at, last_seen_at, tombstone_at,
                            collection_job_id
                        )
                        VALUES(
                            ?, ?, 'active', CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP, NULL, ?
                        )
                        ON CONFLICT(value_index_id) DO UPDATE SET
                            value_count=excluded.value_count,
                            status='active',
                            last_seen_at=CURRENT_TIMESTAMP,
                            tombstone_at=NULL,
                            collection_job_id=excluded.collection_job_id
                        """,
                        (
                            value.id,
                            collected.value_count,
                            collection.collection_job_id,
                        ),
                    )

            for statistics in collection.field_statistics:
                connection.execute(
                    """
                    INSERT INTO nl2sql_field_index_statistics(
                        collection_job_id, database_id, schema_name,
                        raw_table_name, column_name, statistics_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        collection_job_id, database_id,
                        schema_name, raw_table_name, column_name
                    )
                    DO UPDATE SET statistics_json=excluded.statistics_json
                    """,
                    (
                        statistics.collection_job_id,
                        statistics.database_id,
                        statistics.schema_name,
                        statistics.raw_table_name,
                        statistics.column_name,
                        _json(statistics),
                    ),
                )

            for evidence in collection.evidence:
                evidence.id = self._upsert_evidence_sync(connection, evidence)
            for edge in collection.graph_edges:
                edge.id = self._upsert_edge_sync(connection, edge)
        return table_ids

    def _rebuild_fts_sync(self, connection: sqlite3.Connection | None = None) -> None:
        """重建 SQLite FTS 索引，保持结构索引与全文索引一致。"""

        own_connection = connection is None
        connection = connection or self._connect()
        try:
            connection.execute("DELETE FROM nl2sql_table_fts")
            connection.execute(
                """
                INSERT INTO nl2sql_table_fts(row_id, retrieval_text)
                SELECT id, raw_schema_summary || ' ' || random_schema_summary || ' '
                       || raw_table_name || ' ' || random_table_name
                       || ' ' || COALESCE(raw_table_label, '')
                FROM nl2sql_table_indexs
                """
            )
            connection.execute("DELETE FROM nl2sql_value_fts")
            connection.execute(
                """
                INSERT INTO nl2sql_value_fts(row_id, retrieval_text)
                SELECT id, retrieval_text || ' ' || raw_value || ' ' || column_name
                FROM nl2sql_field_value_indexs
                """
            )
            connection.execute("DELETE FROM nl2sql_evidence_fts")
            connection.execute(
                """
                INSERT INTO nl2sql_evidence_fts(row_id, retrieval_text)
                SELECT id, retrieval_text || ' ' || evidence_content
                FROM nl2sql_evidence_indexs
                WHERE status=1
                """
            )
            connection.commit()
        finally:
            if own_connection and self._memory_connection is None:
                connection.close()

    def _upsert_evidence_sync(self, connection, evidence: SQLEvidence) -> int:
        existing = connection.execute(
            """
            SELECT id FROM nl2sql_evidence_indexs
            WHERE evidence_type=? AND evidence_content=?
            ORDER BY id
            LIMIT 1
            """,
            (evidence.evidence_type, evidence.evidence_content),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE nl2sql_evidence_indexs
                SET triggers_json=?, retrieval_text=?, description=?,
                    status=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    _json(evidence.triggers),
                    evidence.retrieval_text or " ".join(evidence.triggers),
                    evidence.description,
                    evidence.status,
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])
        cursor = connection.execute(
            """
            INSERT INTO nl2sql_evidence_indexs(
                triggers_json, retrieval_text, evidence_content,
                evidence_type, description, status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                _json(evidence.triggers),
                evidence.retrieval_text or " ".join(evidence.triggers),
                evidence.evidence_content,
                evidence.evidence_type,
                evidence.description,
                evidence.status,
            ),
        )
        return int(cursor.lastrowid)

    def _upsert_edge_sync(self, connection, edge: SQLGraphEdge) -> int:
        existing = connection.execute(
            """
            SELECT id
            FROM nl2sql_graph_indexs
            WHERE db_type=? AND raw_schema_name=? AND raw_table_name=?
              AND IFNULL(raw_column_name,'')=IFNULL(?,'')
              AND related_raw_schema_name=? AND related_raw_table_name=?
              AND IFNULL(related_raw_column_name,'')=IFNULL(?,'')
              AND relation_type=?
            LIMIT 1
            """,
            (
                edge.db_type,
                edge.raw_schema_name,
                edge.raw_table_name,
                edge.raw_column_name,
                edge.related_raw_schema_name,
                edge.related_raw_table_name,
                edge.related_raw_column_name,
                edge.relation_type,
            ),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE nl2sql_graph_indexs
                SET random_table_name=?, random_column_name=?,
                    related_random_table_name=?, related_random_column_name=?,
                    relation_direction=?, cardinality=?, join_condition=?,
                    evidence_source=?, evidence_content=?, confidence=?,
                    status=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    edge.random_table_name,
                    edge.random_column_name,
                    edge.related_random_table_name,
                    edge.related_random_column_name,
                    edge.relation_direction,
                    edge.cardinality,
                    edge.join_condition,
                    edge.evidence_source,
                    edge.evidence_content,
                    edge.confidence,
                    edge.status,
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])
        connection.execute(
            """
            INSERT INTO nl2sql_graph_indexs(
                db_type, raw_schema_name, related_raw_schema_name,
                raw_table_name, random_table_name, raw_column_name,
                random_column_name, related_raw_table_name,
                related_random_table_name, related_raw_column_name,
                related_random_column_name, relation_type, relation_direction,
                cardinality, join_condition, evidence_source,
                evidence_content, confidence, status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(
                db_type, raw_schema_name, raw_table_name, raw_column_name,
                related_raw_schema_name, related_raw_table_name,
                related_raw_column_name, relation_type
            )
            DO UPDATE SET
                random_table_name=excluded.random_table_name,
                random_column_name=excluded.random_column_name,
                related_random_table_name=excluded.related_random_table_name,
                related_random_column_name=excluded.related_random_column_name,
                relation_direction=excluded.relation_direction,
                cardinality=excluded.cardinality,
                join_condition=excluded.join_condition,
                evidence_source=excluded.evidence_source,
                evidence_content=excluded.evidence_content,
                confidence=excluded.confidence,
                status=excluded.status,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                edge.db_type,
                edge.raw_schema_name,
                edge.related_raw_schema_name,
                edge.raw_table_name,
                edge.random_table_name,
                edge.raw_column_name,
                edge.random_column_name,
                edge.related_raw_table_name,
                edge.related_random_table_name,
                edge.related_raw_column_name,
                edge.related_random_column_name,
                edge.relation_type,
                edge.relation_direction,
                edge.cardinality,
                edge.join_condition,
                edge.evidence_source,
                edge.evidence_content,
                edge.confidence,
                edge.status,
            ),
        )
        row = connection.execute(
            """
            SELECT id FROM nl2sql_graph_indexs
            WHERE db_type=? AND raw_schema_name=? AND raw_table_name=?
              AND IFNULL(raw_column_name, '')=IFNULL(?, '')
              AND related_raw_schema_name=? AND related_raw_table_name=?
              AND IFNULL(related_raw_column_name, '')=IFNULL(?, '')
              AND relation_type=?
            """,
            (
                edge.db_type,
                edge.raw_schema_name,
                edge.raw_table_name,
                edge.raw_column_name,
                edge.related_raw_schema_name,
                edge.related_raw_table_name,
                edge.related_raw_column_name,
                edge.relation_type,
            ),
        ).fetchone()
        return int(row["id"]) if row else 0

    async def list_tables(self, schema_name: str | None = None) -> list[SQLTableSchema]:
        return await asyncio.to_thread(self._list_tables_sync, schema_name)

    async def get_table_hashes(
        self,
        db_type: str,
        *,
        database_id: int = 0,
    ) -> dict[tuple[str, str], str]:
        """读取指定数据库类型已保存的 DDL Hash。

        Args:
            db_type: 目标数据库类型。
            database_id: 目标数据库 ID。

        Returns:
            以 ``(schema_name, table_name)`` 为键的 DDL Hash 映射。
        """

        return await asyncio.to_thread(
            self._get_table_hashes_sync,
            db_type,
            database_id,
        )

    def _get_table_hashes_sync(
        self,
        db_type: str,
        database_id: int,
    ) -> dict[tuple[str, str], str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT raw_schema_name, raw_table_name, raw_ddl_hash
                FROM nl2sql_table_indexs
                WHERE database_id=? AND db_type=?
                """,
                (database_id, db_type),
            ).fetchall()
        return {
            (str(row["raw_schema_name"]), str(row["raw_table_name"])): str(
                row["raw_ddl_hash"]
            )
            for row in rows
        }

    async def compile_sql(
        self,
        sql: str,
        *,
        scope: SQLScope,
    ) -> str:
        """将虚拟表名 SQL 编译为真实表名 SQL，不执行查询。

        Args:
            sql: 使用真实或随机表名的 SQL 文本。
            scope: 表访问范围。

        Returns:
            完成表名替换后的 SQL。

        Raises:
            PermissionError: SQL 引用了 scope 外的表。
            ValueError: SQL 含有无法解析的虚拟表名。
        """

        tables = await self.list_tables()
        aliases: dict[str, str] = {}
        all_names: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
        visible_names: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
        all_qualified: set[tuple[str, str]] = set()
        visible_qualified: set[tuple[str, str]] = set()
        for table in tables:
            schema_key = table.raw_schema_name.casefold()
            for name in (table.raw_table_name, table.random_table_name):
                name_key = name.casefold()
                all_names[name_key].add((schema_key, table.raw_table_name))
                all_qualified.add((schema_key, name_key))
            if not scope.allows_table(
                table.raw_schema_name,
                table.raw_table_name,
                table.random_table_name,
            ):
                continue
            aliases[table.raw_table_name] = table.raw_table_name
            aliases[table.random_table_name] = table.raw_table_name
            for name in (table.raw_table_name, table.random_table_name):
                visible_names[name.casefold()].add(
                    (schema_key, table.raw_table_name)
                )
                visible_qualified.add((schema_key, name.casefold()))
        alias_by_casefold = {name.casefold(): raw for name, raw in aliases.items()}
        table_ref = re.compile(
            r'(?P<prefix>\b(?:from|join|update|into|delete\s+from|truncate\s+table)\s+)'
            r'(?P<schema>(?P<schema_quote>["`]?[A-Za-z_][\w$]*["`]?)\.)?'
            r'(?P<quote>["`]?)'
            r'(?P<name>[A-Za-z_][\w$]*)'
            r'(?P=quote)',
            re.IGNORECASE,
        )
        unauthorized: set[str] = set()
        unknown: set[str] = set()
        for match in table_ref.finditer(sql):
            name = match.group("name")
            schema = (match.group("schema") or "").rstrip(".").strip("\"`")
            name_key = name.casefold()
            if schema:
                key = (schema.casefold(), name_key)
                if key not in all_qualified:
                    unknown.add(f"{schema}.{name}")
                elif key not in visible_qualified:
                    unauthorized.add(f"{schema}.{name}")
            elif not all_names.get(name_key):
                unknown.add(name)
            elif (
                not visible_names.get(name_key)
                or all_names[name_key] != visible_names[name_key]
            ):
                unauthorized.add(name)
        if unknown:
            raise ValueError(
                f"SQL 引用了未收录的表: {', '.join(sorted(unknown))}"
            )
        if unauthorized:
            raise PermissionError(
                f"SQL 引用了当前 scope 无权访问的表: {', '.join(unauthorized)}"
            )
        return table_ref.sub(
            lambda match: (
                match.group("prefix")
                + (match.group("schema") or "")
                + match.group("quote")
                + (
                    alias_by_casefold.get(
                        match.group("name").casefold(),
                        match.group("name"),
                    )
                    if (
                        not match.group("schema")
                        and len(visible_names.get(match.group("name").casefold(), set())) == 1
                    )
                    else (
                        next(
                            (
                                raw
                                for raw_name, raw in aliases.items()
                                if raw_name.casefold() == match.group("name").casefold()
                            ),
                            match.group("name"),
                        )
                        if match.group("schema")
                        else match.group("name")
                    )
                )
                + match.group("quote")
            ),
            sql,
        )

    async def resolve_table_name(
        self,
        table_name: str,
        *,
        scope: SQLScope,
    ) -> str | None:
        """解析一个真实或随机表名，并执行 scope 校验。

        Args:
            table_name: 真实表名或随机表名。
            scope: 表访问范围。

        Returns:
            可访问表的真实表名，未找到或无权限时返回 None。
        """

        table = await self.get_table(table_name)
        if table is None or not scope.allows_table(
            table.raw_schema_name,
            table.raw_table_name,
            table.random_table_name,
        ):
            return None
        return table.raw_table_name

    def _list_tables_sync(self, schema_name: str | None) -> list[SQLTableSchema]:
        with self._connect() as connection:
            if schema_name:
                rows = connection.execute(
                    """
                    SELECT * FROM nl2sql_table_indexs
                    WHERE raw_schema_name=?
                    ORDER BY raw_table_name
                    """,
                    (schema_name,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM nl2sql_table_indexs ORDER BY raw_schema_name, raw_table_name"
                ).fetchall()
        return [_table_from_row(row) for row in rows]

    async def list_schemas(self) -> list[str]:
        return await asyncio.to_thread(self._list_schemas_sync)

    def _list_schemas_sync(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT raw_schema_name
                FROM nl2sql_table_indexs
                ORDER BY raw_schema_name
                """
            ).fetchall()
        return [str(row["raw_schema_name"]) for row in rows]

    async def get_table(
        self,
        table_name: str,
        *,
        schema_name: str | None = None,
    ) -> SQLTableSchema | None:
        return await asyncio.to_thread(self._get_table_sync, table_name, schema_name)

    def _get_table_sync(self, table_name: str, schema_name: str | None) -> SQLTableSchema | None:
        with self._connect() as connection:
            clauses = [
                "(raw_table_name = ? OR random_table_name = ? OR raw_table_label = ?)"
            ]
            args: list[Any] = [table_name, table_name, table_name]
            if schema_name:
                clauses.append("raw_schema_name = ?")
                args.append(schema_name)
            row = connection.execute(
                f"SELECT * FROM nl2sql_table_indexs WHERE {' AND '.join(clauses)} LIMIT 1",
                args,
            ).fetchone()
        return _table_from_row(row) if row else None

    async def search_tables(self, query: str, limit: int = 20) -> list[tuple[SQLTableSchema, float]]:
        return await asyncio.to_thread(self._search_tables_sync, query, limit)

    async def upsert_embeddings(
        self,
        *,
        tables: Sequence[tuple[SQLTableSchema, Sequence[float]]] = (),
        values: Sequence[tuple[SQLFieldValue, Sequence[float]]] = (),
        evidence: Sequence[tuple[SQLEvidence, Sequence[float]]] = (),
        model_name: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._upsert_embeddings_sync,
            tables,
            values,
            evidence,
            model_name or self.embedding_model or "default",
        )

    async def filter_field_values_requiring_embeddings(
        self,
        values: Sequence[SQLFieldValue],
        *,
        model_name: str,
    ) -> list[SQLFieldValue]:
        """筛出新增或检索文本变化的字段值。

        Args:
            values: 本次采集的字段值。
            model_name: Embedding 模型名称。

        Returns:
            需要生成或更新向量的字段值。
        """

        if not values or not self.embedding_dimension:
            return list(values)

        def filter_values() -> list[SQLFieldValue]:
            table_name = _safe_identifier(
                f"nl2sql_field_value_embeddings_{self.embedding_dimension}"
            )
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    SELECT value.database_id, value.schema_name,
                           value.raw_table_name, value.column_name,
                           value.raw_value, embedding.retrieval_text_hash
                    FROM nl2sql_field_value_indexs AS value
                    LEFT JOIN {table_name} AS embedding
                      ON embedding.value_index_id=value.id
                     AND embedding.model_name=?
                    """,
                    (model_name,),
                ).fetchall()
            existing = {
                (
                    int(row["database_id"]),
                    str(row["schema_name"]),
                    str(row["raw_table_name"]),
                    str(row["column_name"]),
                    str(row["raw_value"]),
                ): str(row["retrieval_text_hash"] or "")
                for row in rows
            }
            return [
                value
                for value in values
                if existing.get(
                    (
                        value.database_id,
                        value.schema_name,
                        value.raw_table_name,
                        value.column_name,
                        value.raw_value,
                    ),
                    "",
                )
                != hashlib.sha256(
                    (value.retrieval_text or value.raw_value).encode("utf-8")
                ).hexdigest()
            ]

        return await asyncio.to_thread(filter_values)

    def _upsert_embeddings_sync(self, tables, values, evidence, model_name: str) -> None:
        if not self.embedding_dimension:
            return
        suffix = self.embedding_dimension
        table_embedding = _safe_identifier(f"nl2sql_table_embeddings_{suffix}")
        value_embedding = _safe_identifier(f"nl2sql_field_value_embeddings_{suffix}")
        evidence_embedding = _safe_identifier(f"nl2sql_evidence_embeddings_{suffix}")
        with self._connect() as connection:
            for table, vector in tables:
                row = connection.execute(
                    """
                    SELECT id FROM nl2sql_table_indexs
                    WHERE db_type=? AND raw_schema_name=? AND raw_table_name=?
                    """,
                    (table.db_type, table.raw_schema_name, table.raw_table_name),
                ).fetchone()
                if row:
                    connection.execute(
                        f"""
                        INSERT INTO {table_embedding}(
                            table_index_id, model_name, embedding_json
                        ) VALUES(?, ?, ?)
                        ON CONFLICT(table_index_id, model_name)
                        DO UPDATE SET embedding_json=excluded.embedding_json
                        """,
                        (int(row["id"]), model_name, _json(list(vector))),
                    )
            for value, vector in values:
                row = connection.execute(
                    """
                    SELECT id FROM nl2sql_field_value_indexs
                    WHERE database_id=? AND schema_name=?
                      AND raw_table_name=? AND column_name=? AND raw_value=?
                    """,
                    (
                        value.database_id,
                        value.schema_name,
                        value.raw_table_name,
                        value.column_name,
                        value.raw_value,
                    ),
                ).fetchone()
                if row:
                    connection.execute(
                        f"""
                        INSERT INTO {value_embedding}(
                            value_index_id, model_name,
                            retrieval_text_hash, embedding_json
                        ) VALUES(?, ?, ?, ?)
                        ON CONFLICT(value_index_id, model_name)
                        DO UPDATE SET
                            retrieval_text_hash=excluded.retrieval_text_hash,
                            embedding_json=excluded.embedding_json,
                            created_at=CURRENT_TIMESTAMP
                        """,
                        (
                            int(row["id"]),
                            model_name,
                            hashlib.sha256(
                                (
                                    value.retrieval_text
                                    or value.raw_value
                                ).encode("utf-8")
                            ).hexdigest(),
                            _json(list(vector)),
                        ),
                    )
            for item, vector in evidence:
                if item.id is None:
                    continue
                connection.execute(
                    f"""
                    INSERT INTO {evidence_embedding}(
                        evidence_index_id, model_name, embedding_json
                    ) VALUES(?, ?, ?)
                    ON CONFLICT(evidence_index_id, model_name)
                    DO UPDATE SET embedding_json=excluded.embedding_json
                    """,
                    (int(item.id), model_name, _json(list(vector))),
                )

    async def search_vector(
        self,
        query_vector: Sequence[float],
        *,
        kind: str,
        limit: int = 20,
        model_name: str | None = None,
    ):
        return await asyncio.to_thread(
            self._search_vector_sync,
            list(query_vector),
            kind,
            limit,
            model_name or self.embedding_model or "default",
        )

    def _search_vector_sync(self, query_vector, kind, limit, model_name):
        if not self.embedding_dimension:
            return []
        table_name, payload_factory = {
            "table": (
                _safe_identifier(f"nl2sql_table_embeddings_{self.embedding_dimension}"),
                lambda row: _table_from_row(row),
            ),
            "value": (
                _safe_identifier(f"nl2sql_field_value_embeddings_{self.embedding_dimension}"),
                lambda row: _value_from_row(row),
            ),
            "evidence": (
                _safe_identifier(f"nl2sql_evidence_embeddings_{self.embedding_dimension}"),
                lambda row: _evidence_from_row(row),
            ),
        }.get(kind, (None, None))
        if not table_name:
            raise ValueError(f"unsupported SQL embedding kind: {kind}")
        parent_table = {
            "table": "nl2sql_table_indexs",
            "value": "nl2sql_field_value_indexs",
            "evidence": "nl2sql_evidence_indexs",
        }[kind]
        parent_key = {
            "table": "table_index_id",
            "value": "value_index_id",
            "evidence": "evidence_index_id",
        }[kind]
        with self._connect() as connection:
            runtime_join = (
                """
                JOIN nl2sql_field_value_runtime_states state
                  ON state.value_index_id=p.id
                """
                if kind == "value"
                else ""
            )
            active_clause = (
                "AND state.status='active'"
                if kind == "value"
                else ""
            )
            rows = connection.execute(
                f"""
                SELECT e.embedding_json, p.*
                FROM {table_name} e
                JOIN {parent_table} p ON p.id=e.{parent_key}
                {runtime_join}
                WHERE e.model_name=? {active_clause}
                """,
                (model_name,),
            ).fetchall()
        scored = []
        for row in rows:
            vector = json.loads(row["embedding_json"])
            score = _cosine(query_vector, vector)
            scored.append((payload_factory(row), score))
        scored.sort(key=lambda item: -item[1])
        return scored[: max(1, int(limit))]

    def _search_tables_sync(self, query: str, limit: int) -> list[tuple[SQLTableSchema, float]]:
        terms = _terms(query)
        if not terms:
            return []
        if self._fts_enabled:
            match_query = " OR ".join(
                f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms
            )
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT p.*, bm25(nl2sql_table_fts) AS rank
                    FROM nl2sql_table_fts AS f
                    JOIN nl2sql_table_indexs AS p ON p.id = CAST(f.row_id AS INTEGER)
                    WHERE f.nl2sql_table_fts MATCH ?
                    ORDER BY rank ASC, p.id
                    LIMIT ?
                    """,
                    (match_query, max(1, int(limit))),
                ).fetchall()
            if rows:
                return [
                    (_table_from_row(row), max(0.0, -float(row["rank"] or 0.0)))
                    for row in rows
                ]
        rows = self._list_tables_sync(None)
        scored: list[tuple[SQLTableSchema, float]] = []
        for table in rows:
            haystack = " ".join(
                [
                    table.raw_schema_summary,
                    table.random_schema_summary,
                    table.raw_table_name,
                    table.random_table_name,
                    table.raw_table_label,
                    *(column.name for column in table.columns),
                    *(column.comment for column in table.columns),
                ]
            ).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((table, float(score)))
        scored.sort(key=lambda item: (-item[1], item[0].raw_schema_name, item[0].raw_table_name))
        return scored[: max(1, int(limit))]

    async def search_values(
        self,
        query: str,
        *,
        table_name: str | None = None,
        limit: int = 20,
    ) -> list[tuple[SQLFieldValue, float]]:
        return await asyncio.to_thread(self._search_values_sync, query, table_name, limit)

    async def get_field_value_counts(
        self,
        value_index_ids: Sequence[int],
    ) -> dict[int, int]:
        """批量读取字段值索引记录的数据库引用数量。

        Args:
            value_index_ids: 字段值索引主键集合。

        Returns:
            以字段值索引主键为键的引用数量。
        """

        if not value_index_ids:
            return {}

        def read_counts() -> dict[int, int]:
            placeholders = ",".join("?" for _ in value_index_ids)
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    SELECT value_index_id, value_count
                    FROM nl2sql_field_value_runtime_states
                    WHERE value_index_id IN ({placeholders})
                      AND status='active'
                    """,
                    tuple(int(value) for value in value_index_ids),
                ).fetchall()
            return {
                int(row["value_index_id"]): int(row["value_count"] or 0)
                for row in rows
            }

        return await asyncio.to_thread(read_counts)

    def _search_values_sync(
        self,
        query: str,
        table_name: str | None,
        limit: int,
    ) -> list[tuple[SQLFieldValue, float]]:
        terms = _terms(query)
        if not terms:
            return []
        if self._fts_enabled:
            match_query = " OR ".join(
                f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms
            )
            with self._connect() as connection:
                sql = """
                    SELECT p.*, bm25(nl2sql_value_fts) AS rank
                    FROM nl2sql_value_fts AS f
                    JOIN nl2sql_field_value_indexs AS p
                      ON p.id = CAST(f.row_id AS INTEGER)
                    JOIN nl2sql_field_value_runtime_states AS state
                      ON state.value_index_id=p.id
                    WHERE f.nl2sql_value_fts MATCH ?
                      AND state.status='active'
                """
                args: list[Any] = [match_query]
                if table_name:
                    sql += " AND (p.raw_table_name=? OR p.random_table_name=?)"
                    args.extend([table_name, table_name])
                sql += " ORDER BY rank ASC, p.id LIMIT ?"
                args.append(max(1, int(limit)))
                rows = connection.execute(sql, args).fetchall()
            if rows:
                return [
                    (_value_from_row(row), max(0.0, -float(row["rank"] or 0.0)))
                    for row in rows
                ]
        with self._connect() as connection:
            sql = """
                SELECT value.*
                FROM nl2sql_field_value_indexs AS value
                JOIN nl2sql_field_value_runtime_states AS state
                  ON state.value_index_id=value.id
                WHERE state.status='active'
            """
            args: list[Any] = []
            if table_name:
                sql += (
                    " AND (value.raw_table_name=? "
                    "OR value.random_table_name=?)"
                )
                args.extend([table_name, table_name])
            rows = connection.execute(sql, args).fetchall()
        scored: list[tuple[SQLFieldValue, float]] = []
        for row in rows:
            value = _value_from_row(row)
            haystack = " ".join(
                [value.raw_value, value.retrieval_text, value.column_name, *value.aliases]
            ).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((value, float(score)))
        scored.sort(key=lambda item: (-item[1], item[0].raw_table_name, item[0].column_name))
        return scored[: max(1, int(limit))]

    async def search_evidence(
        self,
        query: str,
        limit: int = 20,
    ) -> list[tuple[SQLEvidence, float]]:
        return await asyncio.to_thread(self._search_evidence_sync, query, limit)

    async def list_evidence(self, limit: int = 1000) -> list[SQLEvidence]:
        return await asyncio.to_thread(self._list_evidence_sync, limit)

    def _list_evidence_sync(self, limit: int) -> list[SQLEvidence]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM nl2sql_evidence_indexs
                WHERE status=1
                ORDER BY id
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [_evidence_from_row(row) for row in rows]

    async def add_evidence_candidate(self, candidate: SQLEvidenceCandidate) -> int:
        return await asyncio.to_thread(self._add_evidence_candidate_sync, candidate)

    def _add_evidence_candidate_sync(self, candidate: SQLEvidenceCandidate) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO nl2sql_evidence_candidate(
                    source_query, candidate_trigger, candidate_evidence,
                    confidence, status
                )
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    candidate.source_query,
                    candidate.candidate_trigger,
                    candidate.candidate_evidence,
                    candidate.confidence,
                    candidate.status,
                ),
            ).fetchone()
            return int(row["id"])

    async def list_evidence_candidates(
        self,
        *,
        status: str = "pending",
        limit: int = 100,
    ) -> list[SQLEvidenceCandidate]:
        return await asyncio.to_thread(
            self._list_evidence_candidates_sync,
            status,
            limit,
        )

    def _list_evidence_candidates_sync(self, status: str, limit: int):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM nl2sql_evidence_candidate
                WHERE status=?
                ORDER BY id
                LIMIT ?
                """,
                (status, max(1, int(limit))),
            ).fetchall()
        return [
            SQLEvidenceCandidate(
                id=int(row["id"]),
                source_query=row["source_query"],
                candidate_trigger=row["candidate_trigger"],
                candidate_evidence=row["candidate_evidence"],
                confidence=float(row["confidence"]),
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def review_evidence_candidate(
        self,
        candidate_id: int,
        *,
        status: str,
        evidence_type: str = "rule",
        description: str = "",
    ) -> SQLEvidence | None:
        return await asyncio.to_thread(
            self._review_evidence_candidate_sync,
            candidate_id,
            status,
            evidence_type,
            description,
        )

    def _review_evidence_candidate_sync(
        self,
        candidate_id: int,
        status: str,
        evidence_type: str,
        description: str,
    ) -> SQLEvidence | None:
        if status not in {"approved", "rejected", "disabled"}:
            raise ValueError("unsupported evidence candidate status")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM nl2sql_evidence_candidate WHERE id=?",
                (candidate_id,),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE nl2sql_evidence_candidate SET status=? WHERE id=?",
                (status, candidate_id),
            )
            if status != "approved":
                return None
            evidence = SQLEvidence(
                triggers=[row["candidate_trigger"]],
                retrieval_text=row["candidate_trigger"],
                evidence_content=row["candidate_evidence"],
                evidence_type=evidence_type,
                description=description,
            )
            evidence.id = self._upsert_evidence_sync(connection, evidence)
            return evidence

    def _search_evidence_sync(self, query: str, limit: int) -> list[tuple[SQLEvidence, float]]:
        terms = _terms(query)
        if not terms:
            return []
        if self._fts_enabled:
            match_query = " OR ".join(
                f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms
            )
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT p.*, bm25(nl2sql_evidence_fts) AS rank
                    FROM nl2sql_evidence_fts AS f
                    JOIN nl2sql_evidence_indexs AS p
                      ON p.id = CAST(f.row_id AS INTEGER)
                    WHERE f.nl2sql_evidence_fts MATCH ?
                      AND p.status=1
                    ORDER BY rank ASC, p.id
                    LIMIT ?
                    """,
                    (match_query, max(1, int(limit))),
                ).fetchall()
            if rows:
                return [
                    (_evidence_from_row(row), max(0.0, -float(row["rank"] or 0.0)))
                    for row in rows
                ]
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM nl2sql_evidence_indexs WHERE status=1"
            ).fetchall()
        scored: list[tuple[SQLEvidence, float]] = []
        for row in rows:
            evidence = _evidence_from_row(row)
            haystack = " ".join(
                [evidence.retrieval_text, evidence.evidence_content, *evidence.triggers]
            ).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((evidence, float(score)))
        scored.sort(key=lambda item: -item[1])
        return scored[: max(1, int(limit))]

    async def list_graph_edges(
        self,
        table_name: str | None = None,
        *,
        include_candidates: bool = True,
        limit: int = 200,
    ) -> list[SQLGraphEdge]:
        return await asyncio.to_thread(
            self._list_graph_edges_sync,
            table_name,
            include_candidates,
            limit,
        )

    def _list_graph_edges_sync(
        self,
        table_name: str | None,
        include_candidates: bool,
        limit: int,
    ) -> list[SQLGraphEdge]:
        with self._connect() as connection:
            clauses = ["status = 'active'"]
            args: list[Any] = []
            if not include_candidates:
                clauses.append("relation_type != 'candidate'")
            if table_name:
                clauses.append(
                    "(raw_table_name=? OR random_table_name=? OR "
                    "related_raw_table_name=? OR related_random_table_name=?)"
                )
                args.extend([table_name] * 4)
            rows = connection.execute(
                f"""
                SELECT * FROM nl2sql_graph_indexs
                WHERE {' AND '.join(clauses)}
                ORDER BY relation_type, confidence DESC, id
                LIMIT ?
                """,
                [*args, max(1, int(limit))],
            ).fetchall()
        return [_edge_from_row(row) for row in rows]

    async def graph_neighbors(
        self,
        table_name: str,
        *,
        max_hops: int = 2,
        include_candidates: bool = True,
        limit: int = 100,
    ) -> list[SQLGraphEdge]:
        edges = await self.list_graph_edges(
            None,
            include_candidates=include_candidates,
            limit=max(1000, limit * 10),
        )
        adjacency: defaultdict[str, list[SQLGraphEdge]] = defaultdict(list)
        for edge in edges:
            left = f"{edge.raw_schema_name}.{edge.raw_table_name}"
            right = f"{edge.related_raw_schema_name}.{edge.related_raw_table_name}"
            adjacency[left].append(edge)
            reverse = edge.model_copy(
                update={
                    "raw_schema_name": edge.related_raw_schema_name,
                    "raw_table_name": edge.related_raw_table_name,
                    "random_table_name": edge.related_random_table_name,
                    "raw_column_name": edge.related_raw_column_name,
                    "random_column_name": edge.related_random_column_name,
                    "related_raw_schema_name": edge.raw_schema_name,
                    "related_raw_table_name": edge.raw_table_name,
                    "related_random_table_name": edge.random_table_name,
                    "related_raw_column_name": edge.raw_column_name,
                    "related_random_column_name": edge.random_column_name,
                }
            )
            adjacency[right].append(reverse)
        start = table_name
        matches = {
            key
            for key in adjacency
            if start in key
            or key.endswith(f".{start}")
            or any(
                start in {
                    edge.random_table_name,
                    edge.related_random_table_name,
                }
                for edge in adjacency[key]
            )
        }
        if not matches:
            matches = {start}
        queue = deque((node, 0) for node in matches)
        visited = set(matches)
        result: list[SQLGraphEdge] = []
        while queue and len(result) < limit:
            node, depth = queue.popleft()
            if depth >= max(0, int(max_hops)):
                continue
            for edge in adjacency.get(node, []):
                result.append(edge)
                target = f"{edge.related_raw_schema_name}.{edge.related_raw_table_name}"
                if target not in visited:
                    visited.add(target)
                    queue.append((target, depth + 1))
                if len(result) >= limit:
                    break
        return result

    async def record_feedback(self, feedback: SQLFeedback) -> list[SQLGraphEdge]:
        return await asyncio.to_thread(self._record_feedback_sync, feedback)

    def _record_feedback_sync(self, feedback: SQLFeedback) -> list[SQLGraphEdge]:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO nl2sql_feedback(
                    source_query, generated_sql, execution_result,
                    used_tables_json, join_conditions_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    feedback.query,
                    feedback.generated_sql,
                    feedback.execution_result,
                    _json(feedback.used_tables),
                    _json(feedback.join_conditions),
                ),
            )
            candidate_evidence = feedback.candidate_evidence or feedback.evidence_content
            if candidate_evidence:
                trigger = feedback.candidate_trigger or feedback.query
                connection.execute(
                    """
                    INSERT INTO nl2sql_evidence_candidate(
                        source_query, candidate_trigger, candidate_evidence,
                        confidence, status
                    )
                    VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (
                        feedback.query,
                        trigger,
                        candidate_evidence,
                        max(0.0, min(1.0, feedback.confidence)),
                    ),
                )
            tables = self._resolve_feedback_tables(connection, feedback.used_tables)
            conditions = _parse_join_conditions(feedback.join_conditions)
            candidates: list[SQLGraphEdge] = []
            for left, right in _table_pairs(tables):
                left_table, right_table = left, right
                left_column, right_column = _resolve_pair_columns(
                    conditions.get(
                    frozenset({left_table.raw_table_name, right_table.raw_table_name}),
                    (None, None),
                    ),
                    left_table,
                    right_table,
                )
                edge = SQLGraphEdge(
                    db_type=left_table.db_type,
                    raw_schema_name=left_table.raw_schema_name,
                    related_raw_schema_name=right_table.raw_schema_name,
                    raw_table_name=left_table.raw_table_name,
                    random_table_name=left_table.random_table_name,
                    raw_column_name=left_column,
                    random_column_name=_random_column(left_table, left_column),
                    related_raw_table_name=right_table.raw_table_name,
                    related_random_table_name=right_table.random_table_name,
                    related_raw_column_name=right_column,
                    related_random_column_name=_random_column(right_table, right_column),
                    relation_type="candidate",
                    relation_direction="bidirectional",
                    cardinality="unknown",
                    join_condition=(
                        f"{left_table.raw_table_name}.{left_column} = "
                        f"{right_table.raw_table_name}.{right_column}"
                        if left_column and right_column
                        else ""
                    ),
                    evidence_source="sql_feedback",
                    evidence_content=(
                        feedback.evidence_content
                        or f"问题: {feedback.query}\nSQL: {feedback.generated_sql}\n"
                        f"执行结果: {feedback.execution_result}"
                    ),
                    confidence=max(0.0, min(1.0, feedback.confidence)),
                    status="active",
                )
                existing = connection.execute(
                    """
                    SELECT * FROM nl2sql_graph_indexs
                    WHERE db_type=?
                      AND (
                        (
                          raw_schema_name=? AND raw_table_name=?
                          AND IFNULL(raw_column_name,'')=IFNULL(?,'')
                          AND related_raw_schema_name=? AND related_raw_table_name=?
                          AND IFNULL(related_raw_column_name,'')=IFNULL(?,'')
                        )
                        OR
                        (
                          raw_schema_name=? AND raw_table_name=?
                          AND IFNULL(raw_column_name,'')=IFNULL(?,'')
                          AND related_raw_schema_name=? AND related_raw_table_name=?
                          AND IFNULL(related_raw_column_name,'')=IFNULL(?,'')
                        )
                      )
                    ORDER BY CASE relation_type
                        WHEN 'physical' THEN 1
                        WHEN 'validated' THEN 2
                        ELSE 3
                    END
                    LIMIT 1
                    """,
                    (
                        edge.db_type,
                        edge.raw_schema_name,
                        edge.raw_table_name,
                        edge.raw_column_name,
                        edge.related_raw_schema_name,
                        edge.related_raw_table_name,
                        edge.related_raw_column_name,
                        edge.related_raw_schema_name,
                        edge.related_raw_table_name,
                        edge.related_raw_column_name,
                        edge.raw_schema_name,
                        edge.raw_table_name,
                        edge.raw_column_name,
                    ),
                ).fetchone()
                if existing and str(existing["relation_type"]) == "physical":
                    continue
                if existing and str(existing["relation_type"]) == "validated":
                    connection.execute(
                        """
                        UPDATE nl2sql_graph_indexs
                        SET evidence_content = evidence_content || ?,
                            confidence = MAX(confidence, ?),
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (
                            f"\n\n{edge.evidence_content}",
                            edge.confidence,
                            int(existing["id"]),
                        ),
                    )
                    continue
                self._upsert_edge_sync(connection, edge)
                candidates.append(edge)
            return candidates

    def _resolve_feedback_tables(
        self,
        connection: sqlite3.Connection,
        table_names: Sequence[str],
    ) -> list[SQLTableSchema]:
        resolved: list[SQLTableSchema] = []
        seen: set[tuple[str, str]] = set()
        for value in table_names:
            name = str(value).strip()
            if not name:
                continue
            parts = name.split(".")
            schema_name = ".".join(parts[:-1]) if len(parts) > 1 else None
            table_name = parts[-1]
            clauses = [
                "(raw_table_name=? OR random_table_name=? OR raw_table_label=?)"
            ]
            args: list[Any] = [table_name, table_name, table_name]
            if schema_name:
                clauses.append("raw_schema_name=?")
                args.append(schema_name)
            row = connection.execute(
                f"SELECT * FROM nl2sql_table_indexs WHERE {' AND '.join(clauses)} LIMIT 1",
                args,
            ).fetchone()
            if not row:
                continue
            table = _table_from_row(row)
            key = (table.raw_schema_name, table.raw_table_name)
            if key not in seen:
                resolved.append(table)
                seen.add(key)
        return resolved

    async def update_graph_status(self, edge_id: int, *, relation_type: str, status: str) -> None:
        await asyncio.to_thread(
            self._update_graph_status_sync,
            edge_id,
            relation_type,
            status,
        )

    def _update_graph_status_sync(self, edge_id: int, relation_type: str, status: str) -> None:
        if relation_type not in {"physical", "candidate", "validated"}:
            raise ValueError("unsupported graph relation_type")
        if status not in {"active", "disabled"}:
            raise ValueError("unsupported graph status")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE nl2sql_graph_indexs
                SET relation_type=?, status=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (relation_type, status, edge_id),
            )

    async def close(self) -> None:
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None


def _json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif isinstance(value, (list, tuple)):
        value = [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in value
        ]
    return json.dumps(value, ensure_ascii=False, default=str)


def _terms(value: str) -> list[str]:
    return [
        term.lower()
        for term in re.findall(r"[\w\u4e00-\u9fff]+", value or "")
        if term.strip()
    ]


def _safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise ValueError(f"invalid SQLite identifier: {value!r}")
    return value


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _table_from_row(row: sqlite3.Row | None) -> SQLTableSchema:
    if row is None:
        raise ValueError("table row is required")
    return SQLTableSchema(
        id=int(row["id"]),
        database_id=int(row["database_id"] or 0),
        db_type=row["db_type"],
        raw_schema_name=row["raw_schema_name"],
        raw_table_name=row["raw_table_name"],
        random_table_name=row["random_table_name"],
        raw_table_label=row["raw_table_label"] or "",
        raw_ddl_text=row["raw_ddl_text"],
        raw_ddl_hash=row["raw_ddl_hash"],
        random_ddl_text=row["random_ddl_text"],
        raw_schema_summary=row["raw_schema_summary"],
        random_schema_summary=row["random_schema_summary"],
        columns=json.loads(row["columns_json"]),
        foreign_keys=json.loads(row["foreign_keys_json"]),
        indexes=json.loads(row["indexes_json"]),
        row_count=row["row_count"],
        sample_rows=json.loads(row["sample_rows_json"]),
        qa_text=row["qa_text"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _value_from_row(row: sqlite3.Row) -> SQLFieldValue:
    return SQLFieldValue(
        id=int(row["id"]),
        database_id=int(row["database_id"]),
        schema_name=row["schema_name"],
        raw_value=row["raw_value"],
        raw_table_name=row["raw_table_name"],
        random_table_name=row["random_table_name"],
        column_name=row["column_name"],
        aliases=json.loads(row["aliases"]),
        retrieval_text=row["retrieval_text"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _annotation_from_row(row: sqlite3.Row) -> SQLFieldValueAnnotation:
    """把 SQLite 行转换为字段值标注对象。

    Args:
        row: 字段值标注查询结果。

    Returns:
        字段值标注对象。
    """

    return SQLFieldValueAnnotation(
        id=int(row["id"]),
        value_index_id=(
            int(row["value_index_id"])
            if row["value_index_id"] is not None
            else None
        ),
        collection_job_id=str(row["collection_job_id"]),
        database_id=int(row["database_id"]),
        schema_name=str(row["schema_name"]),
        raw_table_name=str(row["raw_table_name"]),
        column_name=str(row["column_name"]),
        raw_value=str(row["raw_value"]),
        should_index=bool(row["should_index"]),
        confidence=float(row["confidence"]),
        aliases=json.loads(row["aliases"]),
        reason=str(row["reason"]),
        model_name=str(row["model_name"]),
        prompt_version=str(row["prompt_version"]),
        input_hash=str(row["input_hash"]),
        review_status=str(row["review_status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _evidence_from_row(row: sqlite3.Row) -> SQLEvidence:
    return SQLEvidence(
        id=int(row["id"]),
        triggers=json.loads(row["triggers_json"]),
        retrieval_text=row["retrieval_text"],
        evidence_content=row["evidence_content"],
        evidence_type=row["evidence_type"],
        description=row["description"],
        status=int(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _edge_from_row(row: sqlite3.Row) -> SQLGraphEdge:
    return SQLGraphEdge(
        id=int(row["id"]),
        db_type=row["db_type"],
        raw_schema_name=row["raw_schema_name"],
        related_raw_schema_name=row["related_raw_schema_name"],
        raw_table_name=row["raw_table_name"],
        random_table_name=row["random_table_name"],
        raw_column_name=row["raw_column_name"],
        random_column_name=row["random_column_name"],
        related_raw_table_name=row["related_raw_table_name"],
        related_random_table_name=row["related_random_table_name"],
        related_raw_column_name=row["related_raw_column_name"],
        related_random_column_name=row["related_random_column_name"],
        relation_type=row["relation_type"],
        relation_direction=row["relation_direction"],
        cardinality=row["cardinality"],
        join_condition=row["join_condition"],
        evidence_source=row["evidence_source"],
        evidence_content=row["evidence_content"],
        confidence=float(row["confidence"]),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _random_column(table: SQLTableSchema, column_name: str | None) -> str | None:
    """Return the original column name.

    ``random_column_name`` is kept in the persisted contract for compatibility
    with existing index rows, but column masking is intentionally disabled:
    only table names are protected.
    """

    if not column_name:
        return None
    for column in table.columns:
        if column.name == column_name or column.random_name == column_name:
            return column.name
    return column_name


def _table_pairs(tables: list[SQLTableSchema]) -> list[tuple[SQLTableSchema, SQLTableSchema]]:
    pairs: list[tuple[SQLTableSchema, SQLTableSchema]] = []
    for index, left in enumerate(tables):
        for right in tables[index + 1 :]:
            pairs.append((left, right))
    return pairs


def _parse_join_conditions(
    conditions: Sequence[str],
) -> dict[frozenset[str], tuple[str | None, str | None]]:
    result: dict[frozenset[str], tuple[str | None, str | None]] = {}
    pattern = re.compile(
        r"(?:(?P<left_table>[\w$]+)\.)?(?P<left_column>[\w$]+)\s*=\s*"
        r"(?:(?P<right_table>[\w$]+)\.)?(?P<right_column>[\w$]+)",
        re.IGNORECASE,
    )
    for condition in conditions:
        match = pattern.search(condition or "")
        if not match:
            continue
        left_table = match.group("left_table") or ""
        right_table = match.group("right_table") or ""
        if not left_table or not right_table:
            continue
        result[frozenset({left_table, right_table})] = (
            match.group("left_column"),
            match.group("right_column"),
        )
    return result


def _resolve_pair_columns(
    condition_columns: tuple[str | None, str | None],
    left: SQLTableSchema,
    right: SQLTableSchema,
) -> tuple[str | None, str | None]:
    left_column, right_column = condition_columns
    if left_column in {column.name for column in left.columns}:
        return left_column, right_column
    if right_column in {column.name for column in left.columns}:
        return right_column, left_column
    return left_column, right_column
