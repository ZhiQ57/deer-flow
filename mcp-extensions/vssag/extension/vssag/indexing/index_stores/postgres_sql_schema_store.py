"""PostgreSQL storage adapter for SQL Schema indexes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict, deque
from collections.abc import Sequence
from typing import Any

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
from .sql_schema_store import (
    _parse_join_conditions,
    _resolve_pair_columns,
    _random_column,
)


class PostgresSQLSchemaStore:
    backend = "postgresql"

    def __init__(
        self,
        dsn: str,
        *,
        namespace: str = "sql_schema",
        auto_create_tables: bool = True,
        embedding_dimension: int = 0,
        embedding_model: str = "",
    ):
        self.dsn = (dsn or "").replace("postgresql+asyncpg://", "postgresql://", 1)
        self.namespace = _safe_identifier(namespace)
        self.auto_create_tables = auto_create_tables
        self.embedding_dimension = max(0, int(embedding_dimension))
        self.embedding_model = embedding_model
        self._connection = None
        self._initialized = False
        self._trgm_enabled = False

    async def _connect(self):
        if self._connection is not None:
            return self._connection
        try:
            import asyncpg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PostgreSQL SQL Schema 索引需要 asyncpg，请安装 "
                "ownmindforge-vssag[postgres]"
            ) from exc
        if not self.dsn:
            raise RuntimeError("SQL Schema 索引缺少 retriever_service.dsn")
        self._connection = await asyncpg.connect(self.dsn)
        if self.auto_create_tables:
            await self._initialize()
        return self._connection

    async def _initialize(self) -> None:
        if self._initialized:
            return
        connection = self._connection
        schema = self.namespace
        await connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        await connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_table_indexs (
                id BIGSERIAL PRIMARY KEY,
                database_id BIGINT NOT NULL DEFAULT 0,
                db_type VARCHAR(50) NOT NULL,
                raw_schema_name VARCHAR(128) NOT NULL,
                raw_table_name VARCHAR(128) NOT NULL,
                random_table_name VARCHAR(128) NOT NULL,
                raw_table_label VARCHAR(256),
                raw_ddl_text TEXT NOT NULL,
                raw_ddl_hash VARCHAR(64) NOT NULL,
                random_ddl_text TEXT NOT NULL,
                raw_schema_summary TEXT NOT NULL,
                random_schema_summary TEXT NOT NULL,
                columns_json JSONB NOT NULL,
                foreign_keys_json JSONB NOT NULL,
                indexes_json JSONB NOT NULL,
                row_count BIGINT,
                sample_rows_json JSONB NOT NULL,
                qa_text TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (
                    database_id, db_type, raw_schema_name, raw_table_name
                )
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_value_indexs (
                id BIGSERIAL PRIMARY KEY,
                database_id BIGINT NOT NULL,
                schema_name VARCHAR(128) NOT NULL,
                raw_value TEXT NOT NULL,
                raw_table_name VARCHAR(128) NOT NULL,
                random_table_name VARCHAR(128) NOT NULL,
                column_name VARCHAR(128) NOT NULL,
                aliases JSONB NOT NULL,
                retrieval_text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (
                    database_id, schema_name, raw_table_name,
                    column_name, raw_value
                )
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_value_collection_jobs (
                job_id VARCHAR(128) PRIMARY KEY,
                database_id BIGINT NOT NULL,
                db_type VARCHAR(50) NOT NULL,
                mode VARCHAR(20) NOT NULL,
                status VARCHAR(20) NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ,
                processed_tables BIGINT NOT NULL DEFAULT 0,
                processed_fields BIGINT NOT NULL DEFAULT 0,
                processed_values BIGINT NOT NULL DEFAULT 0,
                error_count BIGINT NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                cdc_start_position TEXT NOT NULL DEFAULT '',
                cdc_end_position TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_index_statistics (
                id BIGSERIAL PRIMARY KEY,
                collection_job_id VARCHAR(128) NOT NULL,
                database_id BIGINT NOT NULL,
                schema_name VARCHAR(128) NOT NULL,
                raw_table_name VARCHAR(128) NOT NULL,
                column_name VARCHAR(128) NOT NULL,
                statistics_json JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (collection_job_id)
                    REFERENCES "{schema}".nl2sql_field_value_collection_jobs(job_id)
                    ON DELETE CASCADE,
                UNIQUE (
                    collection_job_id, database_id,
                    schema_name, raw_table_name, column_name
                )
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_value_collection_checkpoints (
                collection_job_id VARCHAR(128) NOT NULL,
                schema_name VARCHAR(128) NOT NULL,
                raw_table_name VARCHAR(128) NOT NULL,
                column_name VARCHAR(128) NOT NULL,
                last_raw_value TEXT NOT NULL DEFAULT '',
                processed_values BIGINT NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'running',
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (collection_job_id)
                    REFERENCES "{schema}".nl2sql_field_value_collection_jobs(job_id)
                    ON DELETE CASCADE,
                PRIMARY KEY (
                    collection_job_id, schema_name,
                    raw_table_name, column_name
                )
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_value_runtime_states (
                value_index_id BIGINT PRIMARY KEY,
                value_count BIGINT NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                tombstone_at TIMESTAMPTZ,
                collection_job_id VARCHAR(128) NOT NULL,
                FOREIGN KEY (value_index_id)
                    REFERENCES "{schema}".nl2sql_field_value_indexs(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (collection_job_id)
                    REFERENCES "{schema}".nl2sql_field_value_collection_jobs(job_id)
                    ON DELETE RESTRICT
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_value_annotations (
                id BIGSERIAL PRIMARY KEY,
                value_index_id BIGINT,
                collection_job_id VARCHAR(128) NOT NULL,
                database_id BIGINT NOT NULL,
                schema_name VARCHAR(128) NOT NULL,
                raw_table_name VARCHAR(128) NOT NULL,
                column_name VARCHAR(128) NOT NULL,
                raw_value TEXT NOT NULL,
                should_index BOOLEAN NOT NULL,
                confidence NUMERIC(5,4) NOT NULL,
                aliases JSONB NOT NULL,
                reason TEXT NOT NULL,
                model_name VARCHAR(128) NOT NULL,
                prompt_version VARCHAR(128) NOT NULL,
                input_hash VARCHAR(64) NOT NULL,
                review_status VARCHAR(20) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (value_index_id)
                    REFERENCES "{schema}".nl2sql_field_value_indexs(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (collection_job_id)
                    REFERENCES "{schema}".nl2sql_field_value_collection_jobs(job_id)
                    ON DELETE CASCADE,
                UNIQUE (collection_job_id, input_hash)
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_value_cdc_source_positions (
                database_id BIGINT NOT NULL,
                db_type VARCHAR(50) NOT NULL,
                source_name VARCHAR(128) NOT NULL,
                source_position TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (database_id, db_type, source_name)
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_value_cdc_events (
                event_id VARCHAR(64) PRIMARY KEY,
                database_id BIGINT NOT NULL,
                event_json JSONB NOT NULL,
                source_position TEXT NOT NULL,
                source_order NUMERIC(30,0) NOT NULL,
                status VARCHAR(20) NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                transport_name VARCHAR(30) NOT NULL,
                transport_partition VARCHAR(256) NOT NULL,
                transport_position TEXT NOT NULL,
                processed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_value_cdc_field_positions (
                database_id BIGINT NOT NULL,
                schema_name VARCHAR(128) NOT NULL,
                raw_table_name VARCHAR(128) NOT NULL,
                column_name VARCHAR(128) NOT NULL,
                source_position TEXT NOT NULL,
                source_order NUMERIC(30,0) NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    database_id, schema_name, raw_table_name, column_name
                )
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_field_value_cdc_transport_positions (
                database_id BIGINT NOT NULL,
                transport_name VARCHAR(30) NOT NULL,
                transport_partition VARCHAR(256) NOT NULL,
                transport_position TEXT NOT NULL,
                source_position TEXT NOT NULL,
                source_order NUMERIC(30,0) NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    database_id, transport_name, transport_partition
                )
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_evidence_indexs (
                id BIGSERIAL PRIMARY KEY,
                triggers_json JSONB NOT NULL,
                retrieval_text TEXT NOT NULL,
                evidence_content TEXT NOT NULL,
                evidence_type VARCHAR(50) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status SMALLINT NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_graph_indexs (
                id BIGSERIAL PRIMARY KEY,
                db_type VARCHAR(50) NOT NULL,
                raw_schema_name VARCHAR(128) NOT NULL,
                related_raw_schema_name VARCHAR(128) NOT NULL,
                raw_table_name VARCHAR(128) NOT NULL,
                random_table_name VARCHAR(128) NOT NULL,
                raw_column_name VARCHAR(128),
                random_column_name VARCHAR(128),
                related_raw_table_name VARCHAR(128) NOT NULL,
                related_random_table_name VARCHAR(128) NOT NULL,
                related_raw_column_name VARCHAR(128),
                related_random_column_name VARCHAR(128),
                relation_type VARCHAR(20) NOT NULL,
                relation_direction VARCHAR(20) NOT NULL DEFAULT 'bidirectional',
                cardinality VARCHAR(30) NOT NULL DEFAULT 'unknown',
                join_condition TEXT NOT NULL DEFAULT '',
                evidence_source VARCHAR(50) NOT NULL,
                evidence_content TEXT NOT NULL DEFAULT '',
                confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (
                    db_type, raw_schema_name, raw_table_name, raw_column_name,
                    related_raw_schema_name, related_raw_table_name,
                    related_raw_column_name, relation_type
                )
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_feedback (
                id BIGSERIAL PRIMARY KEY,
                source_query TEXT NOT NULL,
                generated_sql TEXT NOT NULL,
                execution_result TEXT NOT NULL,
                used_tables_json JSONB NOT NULL,
                join_conditions_json JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS "{schema}".nl2sql_evidence_candidate (
                id BIGSERIAL PRIMARY KEY,
                source_query TEXT NOT NULL,
                candidate_trigger TEXT NOT NULL,
                candidate_evidence TEXT NOT NULL,
                confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        if self.embedding_dimension:
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            suffix = self.embedding_dimension
            await connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table(f'nl2sql_table_embeddings_{suffix}')} (
                    id BIGSERIAL PRIMARY KEY,
                    table_index_id BIGINT NOT NULL,
                    model_name VARCHAR(100) NOT NULL,
                    embedding vector({suffix}) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (table_index_id, model_name),
                    FOREIGN KEY (table_index_id)
                        REFERENCES "{schema}".nl2sql_table_indexs(id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS {self._table(f'nl2sql_field_value_embeddings_{suffix}')} (
                    id BIGSERIAL PRIMARY KEY,
                    value_index_id BIGINT NOT NULL,
                    model_name VARCHAR(100) NOT NULL,
                    retrieval_text_hash VARCHAR(64) NOT NULL DEFAULT '',
                    embedding vector({suffix}) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (value_index_id, model_name),
                    FOREIGN KEY (value_index_id)
                        REFERENCES "{schema}".nl2sql_field_value_indexs(id)
                        ON DELETE CASCADE
                );
                ALTER TABLE {self._table(f'nl2sql_field_value_embeddings_{suffix}')}
                    ADD COLUMN IF NOT EXISTS retrieval_text_hash VARCHAR(64)
                    NOT NULL DEFAULT '';
                CREATE TABLE IF NOT EXISTS {self._table(f'nl2sql_evidence_embeddings_{suffix}')} (
                    id BIGSERIAL PRIMARY KEY,
                    evidence_index_id BIGINT NOT NULL,
                    model_name VARCHAR(100) NOT NULL,
                    embedding vector({suffix}) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (evidence_index_id, model_name),
                    FOREIGN KEY (evidence_index_id)
                        REFERENCES "{schema}".nl2sql_evidence_indexs(id)
                        ON DELETE CASCADE
                );
                """
            )
        try:
            await connection.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            self._trgm_enabled = True
        except Exception:
            self._trgm_enabled = False
        await connection.execute(
            f'CREATE INDEX IF NOT EXISTS idx_{schema}_table_search '
            f'ON "{schema}".nl2sql_table_indexs USING GIN '
            f'(to_tsvector(\'simple\', raw_schema_summary || \' \' || random_schema_summary || \' \' || raw_table_name || \' \' || random_table_name || \' \' || COALESCE(raw_table_label, \'\')))'
        )
        await connection.execute(
            f'CREATE INDEX IF NOT EXISTS idx_{schema}_value_search '
            f'ON "{schema}".nl2sql_field_value_indexs USING GIN '
            f'(to_tsvector(\'simple\', retrieval_text || \' \' || raw_value || \' \' || column_name))'
        )
        await connection.execute(
            f'CREATE INDEX IF NOT EXISTS idx_{schema}_evidence_search '
            f'ON "{schema}".nl2sql_evidence_indexs USING GIN '
            f'(to_tsvector(\'simple\', retrieval_text || \' \' || evidence_content))'
        )
        if self._trgm_enabled:
            try:
                await connection.execute(
                    f'CREATE INDEX IF NOT EXISTS idx_{schema}_table_trgm '
                    f'ON "{schema}".nl2sql_table_indexs USING GIN '
                    f'((raw_table_name || \' \' || random_table_name || \' \' || COALESCE(raw_table_label, \'\')) gin_trgm_ops)'
                )
                await connection.execute(
                    f'CREATE INDEX IF NOT EXISTS idx_{schema}_value_trgm '
                    f'ON "{schema}".nl2sql_field_value_indexs USING GIN '
                    f'((raw_value || \' \' || retrieval_text || \' \' || column_name) gin_trgm_ops)'
                )
                await connection.execute(
                    f'CREATE INDEX IF NOT EXISTS idx_{schema}_evidence_trgm '
                    f'ON "{schema}".nl2sql_evidence_indexs USING GIN '
                    f'((retrieval_text || \' \' || evidence_content) gin_trgm_ops)'
                )
            except Exception:
                self._trgm_enabled = False
        await connection.execute(
            f'CREATE INDEX IF NOT EXISTS idx_{schema}_value_annotation_cache '
            f'ON "{schema}".nl2sql_field_value_annotations '
            f'(input_hash, model_name, prompt_version, updated_at DESC)'
        )
        self._initialized = True

    def _table(self, name: str) -> str:
        return f'"{self.namespace}".{name}'

    async def begin_collection_job(
        self,
        job: SQLFieldValueCollectionJob,
    ) -> None:
        """创建或恢复 PostgreSQL 字段值采集任务。

        Args:
            job: 待创建或恢复的字段值采集任务。
        """

        connection = await self._connect()
        await connection.execute(
            f"""
            INSERT INTO {self._table('nl2sql_field_value_collection_jobs')}(
                job_id, database_id, db_type, mode, status, started_at,
                cdc_start_position, cdc_end_position
            )
            VALUES($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT(job_id) DO UPDATE SET
                status=EXCLUDED.status,
                started_at=EXCLUDED.started_at,
                error_message='',
                cdc_start_position=EXCLUDED.cdc_start_position,
                cdc_end_position=EXCLUDED.cdc_end_position
            """,
            job.job_id,
            job.database_id,
            job.db_type,
            job.mode,
            job.status,
            job.started_at,
            job.cdc_start_position,
            job.cdc_end_position,
        )

    async def complete_collection_job(
        self,
        job: SQLFieldValueCollectionJob,
    ) -> None:
        """提交 PostgreSQL 字段值采集任务。

        Args:
            job: 已完成、失败或取消的字段值采集任务。
        """

        connection = await self._connect()
        await connection.execute(
            f"""
            UPDATE {self._table('nl2sql_field_value_collection_jobs')}
            SET status=$1, finished_at=$2, processed_tables=$3,
                processed_fields=$4, processed_values=$5,
                error_count=$6, error_message=$7,
                cdc_start_position=$8, cdc_end_position=$9
            WHERE job_id=$10
              AND (status <> 'cancelled' OR $1='cancelled')
            """,
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
        )

    async def cancel_collection_job(self, job_id: str) -> bool:
        """取消运行中或等待恢复的字段值采集任务。

        Args:
            job_id: 字段值采集任务 ID。

        Returns:
            成功取消返回真；任务不存在或已经结束返回假。
        """

        connection = await self._connect()
        result = await connection.execute(
            f"""
            UPDATE {self._table('nl2sql_field_value_collection_jobs')}
            SET status='cancelled',
                finished_at=CURRENT_TIMESTAMP,
                error_message='cancelled by request'
            WHERE job_id=$1
              AND status IN ('running', 'failed', 'paused')
            """,
            job_id,
        )
        return result != "UPDATE 0"

    async def pause_collection_job(self, job_id: str) -> bool:
        """将字段值采集任务标记为可恢复的暂停状态。

        Args:
            job_id: 字段值采集任务 ID。

        Returns:
            任务存在且状态已更新时返回 True。
        """

        connection = await self._connect()
        result = await connection.execute(
            f"""
            UPDATE {self._table('nl2sql_field_value_collection_jobs')}
            SET status='paused',
                finished_at=CURRENT_TIMESTAMP,
                error_message='paused by request'
            WHERE job_id=$1
              AND status IN ('running', 'failed')
            """,
            job_id,
        )
        return result != "UPDATE 0"

    async def is_collection_job_cancelled(self, job_id: str) -> bool:
        """检查字段值采集任务是否已取消。

        Args:
            job_id: 字段值采集任务 ID。

        Returns:
            任务状态为 cancelled 时返回真。
        """

        connection = await self._connect()
        return (
            await connection.fetchval(
                f"""
                SELECT status='cancelled'
                FROM {self._table('nl2sql_field_value_collection_jobs')}
                WHERE job_id=$1
                """,
                job_id,
            )
            is True
        )

    async def save_collection_checkpoint(
        self,
        checkpoint: SQLFieldValueCollectionCheckpoint,
    ) -> None:
        """保存 PostgreSQL 字段值采集断点。

        Args:
            checkpoint: 当前字段的采集断点。
        """

        connection = await self._connect()
        await connection.execute(
            f"""
            INSERT INTO {self._table('nl2sql_field_value_collection_checkpoints')}(
                collection_job_id, schema_name, raw_table_name, column_name,
                last_raw_value, processed_values, status, updated_at
            )
            VALUES($1,$2,$3,$4,$5,$6,$7,CURRENT_TIMESTAMP)
            ON CONFLICT(
                collection_job_id, schema_name, raw_table_name, column_name
            )
            DO UPDATE SET
                last_raw_value=EXCLUDED.last_raw_value,
                processed_values=EXCLUDED.processed_values,
                status=EXCLUDED.status,
                updated_at=CURRENT_TIMESTAMP
            """,
            checkpoint.collection_job_id,
            checkpoint.schema_name,
            checkpoint.raw_table_name,
            checkpoint.column_name,
            checkpoint.last_raw_value,
            checkpoint.processed_values,
            checkpoint.status,
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

        if (
            statistics.collection_job_id != checkpoint.collection_job_id
            or statistics.schema_name != checkpoint.schema_name
            or statistics.raw_table_name != checkpoint.raw_table_name
            or statistics.column_name != checkpoint.column_name
        ):
            raise ValueError("字段值页的统计记录与 checkpoint 不一致")
        connection = await self._connect()
        async with connection.transaction():
            cancelled = await connection.fetchval(
                f"""
                SELECT status='cancelled'
                FROM {self._table('nl2sql_field_value_collection_jobs')}
                WHERE job_id=$1
                """,
                checkpoint.collection_job_id,
            )
            if cancelled is True:
                raise asyncio.CancelledError("字段值采集任务已取消")
            await connection.execute(
                f"""
                INSERT INTO {self._table('nl2sql_field_index_statistics')}(
                    collection_job_id, database_id, schema_name,
                    raw_table_name, column_name, statistics_json
                )
                VALUES($1,$2,$3,$4,$5,$6::jsonb)
                ON CONFLICT(
                    collection_job_id, database_id,
                    schema_name, raw_table_name, column_name
                )
                DO UPDATE SET statistics_json=EXCLUDED.statistics_json
                """,
                statistics.collection_job_id,
                statistics.database_id,
                statistics.schema_name,
                statistics.raw_table_name,
                statistics.column_name,
                _json(statistics),
            )
            for collected in values:
                value = collected.value
                annotation = collected.annotation
                if annotation is None or annotation.should_index:
                    row = await connection.fetchrow(
                        f"""
                        INSERT INTO {self._table('nl2sql_field_value_indexs')}(
                            database_id, schema_name, raw_value,
                            raw_table_name, random_table_name, column_name,
                            aliases, retrieval_text, updated_at
                        )
                        VALUES(
                            $1,$2,$3,$4,$5,$6,$7::jsonb,$8,
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT(
                            database_id, schema_name, raw_table_name,
                            column_name, raw_value
                        )
                        DO UPDATE SET
                            random_table_name=EXCLUDED.random_table_name,
                            aliases=EXCLUDED.aliases,
                            retrieval_text=EXCLUDED.retrieval_text,
                            updated_at=CURRENT_TIMESTAMP
                        RETURNING id
                        """,
                        value.database_id,
                        value.schema_name,
                        value.raw_value,
                        value.raw_table_name,
                        value.random_table_name,
                        value.column_name,
                        _json(value.aliases),
                        value.retrieval_text or value.raw_value,
                    )
                    value.id = int(row["id"])
                    await connection.execute(
                        f"""
                        INSERT INTO {self._table('nl2sql_field_value_runtime_states')}(
                            value_index_id, value_count, status,
                            first_seen_at, last_seen_at, tombstone_at,
                            collection_job_id
                        )
                        VALUES(
                            $1,$2,'active',CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP,NULL,$3
                        )
                        ON CONFLICT(value_index_id) DO UPDATE SET
                            value_count=EXCLUDED.value_count,
                            status='active',
                            last_seen_at=CURRENT_TIMESTAMP,
                            tombstone_at=NULL,
                            collection_job_id=EXCLUDED.collection_job_id
                        """,
                        value.id,
                        collected.value_count,
                        checkpoint.collection_job_id,
                    )
                if annotation is not None:
                    annotation.value_index_id = value.id
                    await connection.execute(
                        f"""
                        INSERT INTO {self._table('nl2sql_field_value_annotations')}(
                            value_index_id, collection_job_id, database_id,
                            schema_name, raw_table_name, column_name, raw_value,
                            should_index, confidence, aliases, reason,
                            model_name, prompt_version, input_hash,
                            review_status, updated_at
                        )
                        VALUES(
                            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,
                            $12,$13,$14,$15,CURRENT_TIMESTAMP
                        )
                        ON CONFLICT(collection_job_id, input_hash)
                        DO UPDATE SET
                            value_index_id=EXCLUDED.value_index_id,
                            should_index=EXCLUDED.should_index,
                            confidence=EXCLUDED.confidence,
                            aliases=EXCLUDED.aliases,
                            reason=EXCLUDED.reason,
                            model_name=EXCLUDED.model_name,
                            prompt_version=EXCLUDED.prompt_version,
                            review_status=EXCLUDED.review_status,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        annotation.value_index_id,
                        annotation.collection_job_id,
                        annotation.database_id,
                        annotation.schema_name,
                        annotation.raw_table_name,
                        annotation.column_name,
                        annotation.raw_value,
                        annotation.should_index,
                        annotation.confidence,
                        _json(annotation.aliases),
                        annotation.reason,
                        annotation.model_name,
                        annotation.prompt_version,
                        annotation.input_hash,
                        annotation.review_status,
                    )
            await connection.execute(
                f"""
                INSERT INTO {self._table('nl2sql_field_value_collection_checkpoints')}(
                    collection_job_id, schema_name, raw_table_name, column_name,
                    last_raw_value, processed_values, status, updated_at
                )
                VALUES($1,$2,$3,$4,$5,$6,$7,CURRENT_TIMESTAMP)
                ON CONFLICT(
                    collection_job_id, schema_name, raw_table_name, column_name
                )
                DO UPDATE SET
                    last_raw_value=EXCLUDED.last_raw_value,
                    processed_values=EXCLUDED.processed_values,
                    status=EXCLUDED.status,
                    updated_at=CURRENT_TIMESTAMP
                """,
                checkpoint.collection_job_id,
                checkpoint.schema_name,
                checkpoint.raw_table_name,
                checkpoint.column_name,
                checkpoint.last_raw_value,
                checkpoint.processed_values,
                checkpoint.status,
            )

    async def reconcile_collection_job(
        self,
        collection: SQLSchemaCollection,
    ) -> None:
        """把完整采集任务未出现的值状态标记为 tombstone。

        Args:
            collection: 本次完整字段值采集结果。
        """

        connection = await self._connect()
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
        rows = await connection.fetch(
            f"""
            SELECT value.id, value.schema_name,
                   value.raw_table_name, value.column_name
            FROM {self._table('nl2sql_field_value_indexs')} AS value
            JOIN {self._table('nl2sql_field_value_runtime_states')} AS state
              ON state.value_index_id=value.id
            WHERE value.database_id=$1
              AND state.status='active'
              AND state.collection_job_id<>$2
            """,
            collection.database_id,
            collection.collection_job_id,
        )
        async with connection.transaction():
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
                await connection.execute(
                    f"""
                    UPDATE {self._table('nl2sql_field_value_runtime_states')}
                    SET status='tombstone',
                        tombstone_at=CURRENT_TIMESTAMP
                    WHERE value_index_id=$1
                    """,
                    int(row["id"]),
                )

    async def find_resumable_collection_job(
        self,
        *,
        database_id: int,
        db_type: str,
        mode: str,
    ) -> SQLFieldValueCollectionJob | None:
        """查找最近一次可恢复的 PostgreSQL 字段值采集任务。

        Args:
            database_id: 目标数据库 ID。
            db_type: 目标数据库类型。
            mode: manual 或 auto。

        Returns:
            最近的运行中或失败任务；不存在时返回空值。
        """

        connection = await self._connect()
        row = await connection.fetchrow(
            f"""
            SELECT *
            FROM {self._table('nl2sql_field_value_collection_jobs')}
            WHERE database_id=$1 AND db_type=$2 AND mode=$3
              AND status IN ('running', 'failed', 'paused')
            ORDER BY started_at DESC
            LIMIT 1
            """,
            database_id,
            db_type,
            mode,
        )
        if row is None:
            return None
        return SQLFieldValueCollectionJob(
            job_id=str(row["job_id"]),
            database_id=int(row["database_id"]),
            db_type=str(row["db_type"]),
            mode=str(row["mode"]),
            status=str(row["status"]),
            started_at=row["started_at"].isoformat(),
            finished_at=(
                row["finished_at"].isoformat()
                if row["finished_at"] is not None
                else None
            ),
            processed_tables=int(row["processed_tables"] or 0),
            processed_fields=int(row["processed_fields"] or 0),
            processed_values=int(row["processed_values"] or 0),
            error_count=int(row["error_count"] or 0),
            error_message=str(row["error_message"] or ""),
            cdc_start_position=str(row["cdc_start_position"] or ""),
            cdc_end_position=str(row["cdc_end_position"] or ""),
        )

    async def list_collection_checkpoints(
        self,
        collection_job_id: str,
    ) -> list[SQLFieldValueCollectionCheckpoint]:
        """读取 PostgreSQL 字段值采集任务断点。

        Args:
            collection_job_id: 字段值采集任务 ID。

        Returns:
            该任务已经保存的字段级断点。
        """

        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM {self._table('nl2sql_field_value_collection_checkpoints')}
            WHERE collection_job_id=$1
            ORDER BY schema_name, raw_table_name, column_name
            """,
            collection_job_id,
        )
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

    async def upsert_collection(self, collection: SQLSchemaCollection) -> dict[str, int]:
        connection = await self._connect()
        table_ids: dict[str, int] = {}
        async with connection.transaction():
            for table in collection.tables:
                row = await connection.fetchrow(
                    f"""
                    INSERT INTO {self._table('nl2sql_table_indexs')}(
                        database_id, db_type, raw_schema_name, raw_table_name,
                        random_table_name,
                        raw_table_label, raw_ddl_text, raw_ddl_hash,
                        random_ddl_text, raw_schema_summary, random_schema_summary,
                        columns_json, foreign_keys_json, indexes_json, row_count,
                        sample_rows_json, qa_text, updated_at
                    )
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,
                           $13::jsonb,$14::jsonb,$15,$16::jsonb,$17,
                           CURRENT_TIMESTAMP)
                    ON CONFLICT(
                        database_id, db_type, raw_schema_name, raw_table_name
                    )
                    DO UPDATE SET
                        random_table_name=EXCLUDED.random_table_name,
                        raw_table_label=EXCLUDED.raw_table_label,
                        raw_ddl_text=EXCLUDED.raw_ddl_text,
                        raw_ddl_hash=EXCLUDED.raw_ddl_hash,
                        random_ddl_text=EXCLUDED.random_ddl_text,
                        raw_schema_summary=EXCLUDED.raw_schema_summary,
                        random_schema_summary=EXCLUDED.random_schema_summary,
                        columns_json=EXCLUDED.columns_json,
                        foreign_keys_json=EXCLUDED.foreign_keys_json,
                        indexes_json=EXCLUDED.indexes_json,
                        row_count=EXCLUDED.row_count,
                        sample_rows_json=EXCLUDED.sample_rows_json,
                        qa_text=EXCLUDED.qa_text,
                        updated_at=CURRENT_TIMESTAMP
                    RETURNING id
                    """,
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
                )
                table_ids[f"{table.raw_schema_name}.{table.raw_table_name}"] = int(row["id"])
            for collected in collection.collected_field_values:
                value = collected.value
                row = await connection.fetchrow(
                    f"""
                    INSERT INTO {self._table('nl2sql_field_value_indexs')}(
                        database_id, schema_name, raw_value,
                        raw_table_name, random_table_name, column_name,
                        aliases, retrieval_text, updated_at
                    )
                    VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,CURRENT_TIMESTAMP)
                    ON CONFLICT(
                        database_id, schema_name, raw_table_name,
                        column_name, raw_value
                    )
                    DO UPDATE SET
                        random_table_name=EXCLUDED.random_table_name,
                        aliases=EXCLUDED.aliases,
                        retrieval_text=EXCLUDED.retrieval_text,
                        updated_at=CURRENT_TIMESTAMP
                    RETURNING id
                    """,
                    value.database_id,
                    value.schema_name,
                    value.raw_value,
                    value.raw_table_name,
                    value.random_table_name,
                    value.column_name,
                    _json(value.aliases),
                    value.retrieval_text or value.raw_value,
                )
                value.id = int(row["id"])
                await connection.execute(
                    f"""
                    INSERT INTO {self._table('nl2sql_field_value_runtime_states')}(
                        value_index_id, value_count, status,
                        first_seen_at, last_seen_at, tombstone_at,
                        collection_job_id
                    )
                    VALUES(
                        $1,$2,'active',CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP,NULL,$3
                    )
                    ON CONFLICT(value_index_id) DO UPDATE SET
                        value_count=EXCLUDED.value_count,
                        status='active',
                        last_seen_at=CURRENT_TIMESTAMP,
                        tombstone_at=NULL,
                        collection_job_id=EXCLUDED.collection_job_id
                    """,
                    value.id,
                    collected.value_count,
                    collection.collection_job_id,
                )
            for statistics in collection.field_statistics:
                await connection.execute(
                    f"""
                    INSERT INTO {self._table('nl2sql_field_index_statistics')}(
                        collection_job_id, database_id, schema_name,
                        raw_table_name, column_name, statistics_json
                    )
                    VALUES($1,$2,$3,$4,$5,$6::jsonb)
                    ON CONFLICT(
                        collection_job_id, database_id,
                        schema_name, raw_table_name, column_name
                    )
                    DO UPDATE SET statistics_json=EXCLUDED.statistics_json
                    """,
                    statistics.collection_job_id,
                    statistics.database_id,
                    statistics.schema_name,
                    statistics.raw_table_name,
                    statistics.column_name,
                    _json(statistics),
                )
            for edge in collection.graph_edges:
                edge.id = await self._upsert_edge(connection, edge)
            for evidence in collection.evidence:
                existing = await connection.fetchrow(
                    f"""
                    SELECT id FROM {self._table('nl2sql_evidence_indexs')}
                    WHERE evidence_type=$1 AND evidence_content=$2
                    ORDER BY id LIMIT 1
                    """,
                    evidence.evidence_type,
                    evidence.evidence_content,
                )
                if existing:
                    evidence.id = int(existing["id"])
                    await connection.execute(
                        f"""
                        UPDATE {self._table('nl2sql_evidence_indexs')}
                        SET triggers_json=$1::jsonb, retrieval_text=$2,
                            description=$3, status=$4, updated_at=CURRENT_TIMESTAMP
                        WHERE id=$5
                        """,
                        _json(evidence.triggers),
                        evidence.retrieval_text or " ".join(evidence.triggers),
                        evidence.description,
                        evidence.status,
                        evidence.id,
                    )
                else:
                    row = await connection.fetchrow(
                        f"""
                        INSERT INTO {self._table('nl2sql_evidence_indexs')}(
                            triggers_json, retrieval_text, evidence_content,
                            evidence_type, description, status
                        )
                        VALUES($1::jsonb,$2,$3,$4,$5,$6)
                        RETURNING id
                        """,
                        _json(evidence.triggers),
                        evidence.retrieval_text or " ".join(evidence.triggers),
                        evidence.evidence_content,
                        evidence.evidence_type,
                        evidence.description,
                        evidence.status,
                    )
                    evidence.id = int(row["id"])
        return table_ids

    async def replace_collection(
        self,
        collection: SQLSchemaCollection,
        *,
        changed_tables: set[tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT id, raw_schema_name, raw_table_name
            FROM {self._table('nl2sql_table_indexs')}
            WHERE database_id=$1 AND db_type=$2
            """,
            collection.database_id,
            collection.db_type,
        )
        existing = {
            (str(row["raw_schema_name"]), str(row["raw_table_name"])): int(row["id"])
            for row in rows
        }
        keep = {
            (table.raw_schema_name, table.raw_table_name)
            for table in collection.tables
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
        async with connection.transaction():
            for schema_name, table_name in affected:
                await connection.execute(
                    f"""
                    DELETE FROM {self._table('nl2sql_graph_indexs')}
                    WHERE db_type=$1
                      AND (
                        (raw_schema_name=$2 AND raw_table_name=$3)
                        OR (related_raw_schema_name=$2 AND related_raw_table_name=$3)
                      )
                      AND (
                        relation_type='physical'
                        OR (relation_type='candidate' AND evidence_source='model_inference')
                      )
                    """,
                    collection.db_type,
                    schema_name,
                    table_name,
                )
                columns = current_columns.get((schema_name, table_name))
                if columns:
                    await connection.execute(
                        f"""
                        DELETE FROM {self._table('nl2sql_field_value_indexs')}
                        WHERE database_id=$1
                          AND schema_name=$2
                          AND raw_table_name=$3
                          AND NOT (column_name=ANY($4::varchar[]))
                        """,
                        collection.database_id,
                        schema_name,
                        table_name,
                        sorted(columns),
                    )
                else:
                    await connection.execute(
                        f"""
                        DELETE FROM {self._table('nl2sql_field_value_indexs')}
                        WHERE database_id=$1
                          AND schema_name=$2 AND raw_table_name=$3
                        """,
                        collection.database_id,
                        schema_name,
                        table_name,
                    )
                if self.embedding_dimension:
                    await connection.execute(
                        f"""
                        DELETE FROM {self._table(f'nl2sql_field_value_embeddings_{self.embedding_dimension}')}
                        WHERE value_index_id NOT IN (
                            SELECT id FROM {self._table('nl2sql_field_value_indexs')}
                        )
                        """
                    )
                table_id = existing.get((schema_name, table_name))
                if table_id is not None:
                    if self.embedding_dimension:
                        await connection.execute(
                            f"""
                            DELETE FROM {self._table(f'nl2sql_table_embeddings_{self.embedding_dimension}')}
                            WHERE table_index_id=$1
                            """,
                            table_id,
                        )
                    await connection.execute(
                        f"DELETE FROM {self._table('nl2sql_table_indexs')} WHERE id=$1",
                        table_id,
                    )
            delta = collection.model_copy(
                update={
                    "tables": [
                        table
                        for table in collection.tables
                        if (table.raw_schema_name, table.raw_table_name) in changed
                    ],
                    # 字段值属于采集任务一致性域，未变化表也要更新运行状态，
                    # 避免完整校准把仍然存在的值误标为 tombstone。
                    "collected_field_values": list(
                        collection.collected_field_values
                    ),
                    "field_statistics": list(collection.field_statistics),
                    "graph_edges": [
                        edge
                        for edge in collection.graph_edges
                        if changed_tables is None
                        or (
                            (edge.raw_schema_name, edge.raw_table_name) in changed
                            or (
                                edge.related_raw_schema_name,
                                edge.related_raw_table_name,
                            )
                            in changed
                        )
                    ],
                }
            )
        result = await self.upsert_collection(delta)
        return result

    async def sync_collection(
        self,
        collection: SQLSchemaCollection,
        *,
        changed_tables: set[tuple[str, str]],
        removed_tables: set[tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        """按 DDL Hash 差异同步 PostgreSQL SQL Schema 索引。"""

        del removed_tables
        return await self.replace_collection(
            collection,
            changed_tables=set(changed_tables),
        )

    async def _upsert_edge(self, connection, edge: SQLGraphEdge) -> int:
        row = await connection.fetchrow(
            f"""
            INSERT INTO {self._table('nl2sql_graph_indexs')}(
                db_type, raw_schema_name, related_raw_schema_name,
                raw_table_name, random_table_name, raw_column_name,
                random_column_name, related_raw_table_name,
                related_random_table_name, related_raw_column_name,
                related_random_column_name, relation_type, relation_direction,
                cardinality, join_condition, evidence_source,
                evidence_content, confidence, status
            )
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
            ON CONFLICT(
                db_type, raw_schema_name, raw_table_name, raw_column_name,
                related_raw_schema_name, related_raw_table_name,
                related_raw_column_name, relation_type
            )
            DO UPDATE SET
                random_table_name=EXCLUDED.random_table_name,
                random_column_name=EXCLUDED.random_column_name,
                related_random_table_name=EXCLUDED.related_random_table_name,
                related_random_column_name=EXCLUDED.related_random_column_name,
                relation_direction=EXCLUDED.relation_direction,
                cardinality=EXCLUDED.cardinality,
                join_condition=EXCLUDED.join_condition,
                evidence_source=EXCLUDED.evidence_source,
                evidence_content=EXCLUDED.evidence_content,
                confidence=EXCLUDED.confidence,
                status=EXCLUDED.status,
                updated_at=CURRENT_TIMESTAMP
            RETURNING id
            """,
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
        )
        return int(row["id"])

    async def list_tables(self, schema_name: str | None = None) -> list[SQLTableSchema]:
        connection = await self._connect()
        if schema_name:
            rows = await connection.fetch(
                f"""
                SELECT * FROM {self._table('nl2sql_table_indexs')}
                WHERE raw_schema_name=$1
                ORDER BY raw_table_name
                """,
                schema_name,
            )
        else:
            rows = await connection.fetch(
                f"""
                SELECT * FROM {self._table('nl2sql_table_indexs')}
                ORDER BY raw_schema_name, raw_table_name
                """
            )
        return [_table_from_row(row) for row in rows]

    async def get_table_hashes(
        self,
        db_type: str,
        *,
        database_id: int = 0,
    ) -> dict[tuple[str, str], str]:
        """读取指定数据库已保存的 DDL Hash。

        Args:
            db_type: 目标数据库类型。
            database_id: 目标数据库 ID。

        Returns:
            以 ``(schema_name, table_name)`` 为键的 DDL Hash。
        """

        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT raw_schema_name, raw_table_name, raw_ddl_hash
            FROM {self._table('nl2sql_table_indexs')}
            WHERE database_id=$1 AND db_type=$2
            """,
            database_id,
            db_type,
        )
        return {
            (str(row["raw_schema_name"]), str(row["raw_table_name"])): str(row["raw_ddl_hash"])
            for row in rows
        }

    async def resolve_table_name(
        self,
        table_name: str,
        *,
        scope: SQLScope,
    ) -> str | None:
        table = await self.get_table(table_name)
        if table is None or not scope.allows_table(
            table.raw_schema_name,
            table.raw_table_name,
            table.random_table_name,
        ):
            return None
        return table.raw_table_name

    async def compile_sql(self, sql: str, *, scope: SQLScope) -> str:
        tables = await self.list_tables()
        aliases: dict[str, str] = {}
        all_names: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
        visible_names: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
        all_qualified: set[tuple[str, str]] = set()
        visible_qualified: set[tuple[str, str]] = set()
        for table in tables:
            schema_key = table.raw_schema_name.casefold()
            for name in (table.raw_table_name, table.random_table_name):
                all_names[name.casefold()].add((schema_key, table.raw_table_name))
                all_qualified.add((schema_key, name.casefold()))
            if scope.allows_table(
                table.raw_schema_name,
                table.raw_table_name,
                table.random_table_name,
            ):
                aliases[table.raw_table_name] = table.raw_table_name
                aliases[table.random_table_name] = table.raw_table_name
                for name in (table.raw_table_name, table.random_table_name):
                    visible_names[name.casefold()].add((schema_key, table.raw_table_name))
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
        unknown: set[str] = set()
        unauthorized: set[str] = set()
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
            elif not visible_names.get(name_key) or all_names[name_key] != visible_names[name_key]:
                unauthorized.add(name)
        if unknown:
            raise ValueError(f"SQL 引用了未收录的表: {', '.join(sorted(unknown))}")
        if unauthorized:
            raise PermissionError(
                f"SQL 引用了当前 scope 无权访问的表: {', '.join(sorted(unauthorized))}"
            )
        return table_ref.sub(
            lambda match: (
                match.group("prefix")
                + (match.group("schema") or "")
                + match.group("quote")
                + alias_by_casefold.get(
                    match.group("name").casefold(),
                    match.group("name"),
                )
                + match.group("quote")
            ),
            sql,
        )

    async def list_schemas(self) -> list[str]:
        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT DISTINCT raw_schema_name
            FROM {self._table('nl2sql_table_indexs')}
            ORDER BY raw_schema_name
            """
        )
        return [str(row["raw_schema_name"]) for row in rows]

    async def get_table(self, table_name: str, *, schema_name: str | None = None):
        connection = await self._connect()
        if schema_name:
            row = await connection.fetchrow(
                f"""
                SELECT * FROM {self._table('nl2sql_table_indexs')}
                WHERE raw_schema_name=$1
                  AND (raw_table_name=$2 OR random_table_name=$2 OR raw_table_label=$2)
                LIMIT 1
                """,
                schema_name,
                table_name,
            )
        else:
            row = await connection.fetchrow(
                f"""
                SELECT * FROM {self._table('nl2sql_table_indexs')}
                WHERE raw_table_name=$1 OR random_table_name=$1 OR raw_table_label=$1
                LIMIT 1
                """,
                table_name,
            )
        return _table_from_row(row) if row else None

    async def search_tables(self, query: str, limit: int = 20):
        connection = await self._connect()
        trigram = (
            " OR (raw_table_name || ' ' || random_table_name || ' ' || "
            "COALESCE(raw_table_label, '')) % $1"
            if self._trgm_enabled
            else ""
        )
        rows = await connection.fetch(
            f"""
            SELECT *,
              ts_rank_cd(
                to_tsvector('simple',
                  raw_schema_summary || ' ' || random_schema_summary || ' ' ||
                  raw_table_name || ' ' || random_table_name || ' ' ||
                  COALESCE(raw_table_label, '')),
                plainto_tsquery('simple', $1)
              )
              {(" + similarity(raw_table_name || ' ' || random_table_name, $1)" if self._trgm_enabled else "")}
              AS score
            FROM {self._table('nl2sql_table_indexs')}
            WHERE to_tsvector('simple',
                  raw_schema_summary || ' ' || random_schema_summary || ' ' ||
                  raw_table_name || ' ' || random_table_name || ' ' ||
                  COALESCE(raw_table_label, ''))
                  @@ plainto_tsquery('simple', $1)
              {trigram}
            ORDER BY score DESC, id
            LIMIT $2
            """,
            query,
            max(1, int(limit)),
        )
        return [(_table_from_row(row), float(row["score"] or 0.0)) for row in rows]

    async def upsert_embeddings(
        self,
        *,
        tables: Sequence[tuple[SQLTableSchema, Sequence[float]]] = (),
        values: Sequence[tuple[SQLFieldValue, Sequence[float]]] = (),
        evidence: Sequence[tuple[SQLEvidence, Sequence[float]]] = (),
        model_name: str | None = None,
    ) -> None:
        if not self.embedding_dimension:
            return
        connection = await self._connect()
        model = model_name or self.embedding_model or "default"
        suffix = self.embedding_dimension
        async with connection.transaction():
            for table, vector in tables:
                row = await connection.fetchrow(
                    f"""
                    SELECT id FROM {self._table('nl2sql_table_indexs')}
                    WHERE database_id=$1 AND db_type=$2
                      AND raw_schema_name=$3 AND raw_table_name=$4
                    """,
                    table.database_id,
                    table.db_type,
                    table.raw_schema_name,
                    table.raw_table_name,
                )
                if row:
                    await connection.execute(
                        f"""
                        INSERT INTO {self._table(f'nl2sql_table_embeddings_{suffix}')}(
                            table_index_id, model_name, embedding
                        ) VALUES($1,$2,$3::vector)
                        ON CONFLICT(table_index_id, model_name)
                        DO UPDATE SET embedding=EXCLUDED.embedding
                        """,
                        int(row["id"]),
                        model,
                        _vector_literal(vector),
                    )
            for value, vector in values:
                row = await connection.fetchrow(
                    f"""
                    SELECT id FROM {self._table('nl2sql_field_value_indexs')}
                    WHERE database_id=$1 AND schema_name=$2
                      AND raw_table_name=$3 AND column_name=$4
                      AND raw_value=$5
                    """,
                    value.database_id,
                    value.schema_name,
                    value.raw_table_name,
                    value.column_name,
                    value.raw_value,
                )
                if row:
                    await connection.execute(
                        f"""
                        INSERT INTO {self._table(f'nl2sql_field_value_embeddings_{suffix}')}(
                            value_index_id, model_name,
                            retrieval_text_hash, embedding
                        ) VALUES($1,$2,$3,$4::vector)
                        ON CONFLICT(value_index_id, model_name)
                        DO UPDATE SET
                            retrieval_text_hash=EXCLUDED.retrieval_text_hash,
                            embedding=EXCLUDED.embedding,
                            created_at=CURRENT_TIMESTAMP
                        """,
                        int(row["id"]),
                        model,
                        hashlib.sha256(
                            (
                                value.retrieval_text
                                or value.raw_value
                            ).encode("utf-8")
                        ).hexdigest(),
                        _vector_literal(vector),
                    )
            for item, vector in evidence:
                if item.id is None:
                    continue
                await connection.execute(
                    f"""
                    INSERT INTO {self._table(f'nl2sql_evidence_embeddings_{suffix}')}(
                        evidence_index_id, model_name, embedding
                    ) VALUES($1,$2,$3::vector)
                    ON CONFLICT(evidence_index_id, model_name)
                    DO UPDATE SET embedding=EXCLUDED.embedding
                    """,
                    item.id,
                    model,
                    _vector_literal(vector),
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
        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT value.database_id, value.schema_name,
                   value.raw_table_name, value.column_name,
                   value.raw_value, embedding.retrieval_text_hash
            FROM {self._table('nl2sql_field_value_indexs')} AS value
            LEFT JOIN {
                self._table(
                    f'nl2sql_field_value_embeddings_{self.embedding_dimension}'
                )
            } AS embedding
              ON embedding.value_index_id=value.id
             AND embedding.model_name=$1
            """,
            model_name,
        )
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

    async def search_vector(
        self,
        query_vector: Sequence[float],
        *,
        kind: str,
        limit: int = 20,
        model_name: str | None = None,
    ):
        if not self.embedding_dimension:
            return []
        connection = await self._connect()
        suffix = self.embedding_dimension
        names = {
            "table": (
                f"nl2sql_table_embeddings_{suffix}",
                "nl2sql_table_indexs",
                "table_index_id",
                _table_from_row,
            ),
            "value": (
                f"nl2sql_field_value_embeddings_{suffix}",
                "nl2sql_field_value_indexs",
                "value_index_id",
                _value_from_row,
            ),
            "evidence": (
                f"nl2sql_evidence_embeddings_{suffix}",
                "nl2sql_evidence_indexs",
                "evidence_index_id",
                _evidence_from_row,
            ),
        }
        if kind not in names:
            raise ValueError(f"unsupported SQL embedding kind: {kind}")
        embedding_table, parent_table, parent_key, factory = names[kind]
        model = model_name or self.embedding_model or "default"
        if kind == "value":
            rows = await connection.fetch(
                f"""
                SELECT 1 - (e.embedding <=> $2::vector) AS score, p.*
                FROM {self._table(embedding_table)} e
                JOIN {self._table(parent_table)} p ON p.id=e.{parent_key}
                JOIN {self._table('nl2sql_field_value_runtime_states')} state
                  ON state.value_index_id=p.id
                WHERE e.model_name=$1 AND state.status='active'
                ORDER BY e.embedding <=> $2::vector
                LIMIT $3
                """,
                model,
                _vector_literal(query_vector),
                max(1, int(limit)),
            )
        else:
            rows = await connection.fetch(
                f"""
                SELECT 1 - (e.embedding <=> $2::vector) AS score, p.*
                FROM {self._table(embedding_table)} e
                JOIN {self._table(parent_table)} p ON p.id=e.{parent_key}
                WHERE e.model_name=$1
                ORDER BY e.embedding <=> $2::vector
                LIMIT $3
                """,
                model,
                _vector_literal(query_vector),
                max(1, int(limit)),
            )
        return [
            (factory(row), float(row["score"] or 0.0))
            for row in rows
        ]

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

        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT value.*, state.value_count
            FROM {self._table('nl2sql_field_value_indexs')} value
            JOIN {self._table('nl2sql_field_value_runtime_states')} state
              ON state.value_index_id=value.id
            WHERE state.collection_job_id=$1
              AND state.status='active'
            ORDER BY value.schema_name, value.raw_table_name,
                     value.column_name, value.raw_value
            """,
            collection_job_id,
        )
        return [
            SQLCollectedFieldValue(
                value=_value_from_row(row),
                value_count=int(row["value_count"] or 0),
            )
            for row in rows
        ]

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
        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT DISTINCT ON (input_hash) *
            FROM {self._table('nl2sql_field_value_annotations')}
            WHERE input_hash=ANY($1::varchar[])
              AND model_name=$2
              AND prompt_version=$3
            ORDER BY input_hash, updated_at DESC, id DESC
            """,
            [str(value) for value in input_hashes],
            model_name,
            prompt_version,
        )
        return {
            str(row["input_hash"]): _annotation_from_row(row)
            for row in rows
        }

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
        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM {self._table('nl2sql_field_value_annotations')}
            WHERE review_status=$1
            ORDER BY updated_at, id
            LIMIT $2
            """,
            status,
            max(1, int(limit)),
        )
        return [_annotation_from_row(row) for row in rows]

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
        connection = await self._connect()
        async with connection.transaction():
            row = await connection.fetchrow(
                f"""
                SELECT *
                FROM {self._table('nl2sql_field_value_annotations')}
                WHERE id=$1
                FOR UPDATE
                """,
                int(annotation_id),
            )
            if row is None:
                return None
            updated = await connection.fetchrow(
                f"""
                UPDATE {self._table('nl2sql_field_value_annotations')}
                SET should_index=$1,
                    aliases=$2::jsonb,
                    reason=$3,
                    review_status=$4,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=$5
                RETURNING *
                """,
                status == "approved",
                _json(
                    cleaned_aliases
                    if cleaned_aliases is not None
                    else _json_value(row["aliases"])
                ),
                str(reason)[:1000]
                if reason is not None
                else str(row["reason"]),
                status,
                int(annotation_id),
            )
        return _annotation_from_row(updated)

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

        connection = await self._connect()
        return str(
            await connection.fetchval(
                f"""
                SELECT source_position
                FROM {self._table('nl2sql_field_value_cdc_source_positions')}
                WHERE database_id=$1 AND db_type=$2 AND source_name=$3
                """,
                database_id,
                db_type,
                source_name,
            )
            or ""
        )

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

        connection = await self._connect()
        await connection.execute(
            f"""
            INSERT INTO {self._table('nl2sql_field_value_cdc_source_positions')}(
                database_id, db_type, source_name, source_position, updated_at
            )
            VALUES($1,$2,$3,$4,CURRENT_TIMESTAMP)
            ON CONFLICT(database_id, db_type, source_name)
            DO UPDATE SET
                source_position=EXCLUDED.source_position,
                updated_at=CURRENT_TIMESTAMP
            """,
            database_id,
            db_type,
            source_name,
            source_position,
        )

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

        connection = await self._connect()
        await connection.execute(
            f"""
            UPDATE {self._table('nl2sql_field_value_collection_jobs')}
            SET cdc_start_position=$1,
                cdc_end_position=$2
            WHERE job_id=$3
            """,
            start_position,
            end_position,
            collection_job_id,
        )

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

        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT statistics.statistics_json
            FROM {self._table('nl2sql_field_index_statistics')} statistics
            JOIN {self._table('nl2sql_field_value_collection_jobs')} job
              ON job.job_id=statistics.collection_job_id
            WHERE statistics.database_id=$1
              AND job.status='completed'
            ORDER BY statistics.id DESC
            """,
            database_id,
        )
        tables: set[tuple[str, str]] = set()
        for row in rows:
            statistics = SQLFieldIndexStatistics.model_validate(
                _json_value(row["statistics_json"])
            )
            if statistics.selection_decision in {
                "manual",
                "candidate",
                "llm_reviewed",
                "uncertain",
            }:
                tables.add(
                    (statistics.schema_name, statistics.raw_table_name)
                )
        return sorted(tables)

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

        connection = await self._connect()
        row = await connection.fetchrow(
            f"""
            SELECT statistics.statistics_json,
                   table_index.random_table_name
            FROM {self._table('nl2sql_field_index_statistics')} statistics
            JOIN {self._table('nl2sql_field_value_collection_jobs')} job
              ON job.job_id=statistics.collection_job_id
            JOIN {self._table('nl2sql_table_indexs')} table_index
              ON table_index.database_id=statistics.database_id
             AND table_index.raw_schema_name=statistics.schema_name
             AND table_index.raw_table_name=statistics.raw_table_name
            WHERE statistics.database_id=$1
              AND statistics.schema_name=$2
              AND statistics.raw_table_name=$3
              AND statistics.column_name=$4
              AND job.status='completed'
            ORDER BY statistics.id DESC
            LIMIT 1
            """,
            database_id,
            schema_name,
            raw_table_name,
            column_name,
        )
        if row is None:
            return None
        return (
            SQLFieldIndexStatistics.model_validate(
                _json_value(row["statistics_json"])
            ),
            str(row["random_table_name"]),
        )

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

        if any(
            update.event_id != message.event.event_id for update in updates
        ):
            raise ValueError("CDC 字段更新与消息 event_id 不一致")
        connection = await self._connect()
        async with connection.transaction():
            duplicate = await connection.fetchval(
                f"""
                SELECT 1
                FROM {self._table('nl2sql_field_value_cdc_events')}
                WHERE event_id=$1
                """,
                message.event.event_id,
            )
            if duplicate:
                return "duplicate"
            applied = False
            for update in updates:
                previous = await connection.fetchrow(
                    f"""
                    SELECT source_order
                    FROM {self._table('nl2sql_field_value_cdc_field_positions')}
                    WHERE database_id=$1 AND schema_name=$2
                      AND raw_table_name=$3 AND column_name=$4
                    """,
                    update.database_id,
                    update.schema_name,
                    update.raw_table_name,
                    update.column_name,
                )
                if (
                    previous is not None
                    and update.source_order < int(previous["source_order"])
                ):
                    continue
                if (
                    update.old_value is not None
                    and update.old_value != update.new_value
                ):
                    old_row = await connection.fetchrow(
                        f"""
                        SELECT value.id, state.value_count
                        FROM {self._table('nl2sql_field_value_indexs')} value
                        JOIN {self._table('nl2sql_field_value_runtime_states')} state
                          ON state.value_index_id=value.id
                        WHERE value.database_id=$1
                          AND value.schema_name=$2
                          AND value.raw_table_name=$3
                          AND value.column_name=$4
                          AND value.raw_value=$5
                        """,
                        update.database_id,
                        update.schema_name,
                        update.raw_table_name,
                        update.column_name,
                        update.old_value,
                    )
                    if old_row is not None:
                        next_count = max(
                            0,
                            int(old_row["value_count"] or 0) - 1,
                        )
                        await connection.execute(
                            f"""
                            UPDATE {self._table('nl2sql_field_value_runtime_states')}
                            SET value_count=$1,
                                status=$2,
                                last_seen_at=CURRENT_TIMESTAMP,
                                tombstone_at=CASE
                                  WHEN $1=0 THEN CURRENT_TIMESTAMP
                                  ELSE NULL
                                END
                            WHERE value_index_id=$3
                            """,
                            next_count,
                            "tombstone" if next_count == 0 else "active",
                            int(old_row["id"]),
                        )
                        applied = True
                value_index_id: int | None = None
                annotation = update.annotation
                if (
                    update.new_value is not None
                    and update.new_value != update.old_value
                    and (annotation is None or annotation.should_index)
                ):
                    aliases = annotation.aliases if annotation else []
                    new_row = await connection.fetchrow(
                        f"""
                        INSERT INTO {self._table('nl2sql_field_value_indexs')}(
                            database_id, schema_name, raw_value,
                            raw_table_name, random_table_name, column_name,
                            aliases, retrieval_text, updated_at
                        )
                        VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,CURRENT_TIMESTAMP)
                        ON CONFLICT(
                            database_id, schema_name, raw_table_name,
                            column_name, raw_value
                        )
                        DO UPDATE SET
                            random_table_name=EXCLUDED.random_table_name,
                            aliases=EXCLUDED.aliases,
                            retrieval_text=EXCLUDED.retrieval_text,
                            updated_at=CURRENT_TIMESTAMP
                        RETURNING id
                        """,
                        update.database_id,
                        update.schema_name,
                        update.new_value,
                        update.raw_table_name,
                        update.random_table_name,
                        update.column_name,
                        _json(aliases),
                        " ".join([update.new_value, *aliases]).strip(),
                    )
                    value_index_id = int(new_row["id"])
                    await connection.execute(
                        f"""
                        INSERT INTO {self._table('nl2sql_field_value_runtime_states')}(
                            value_index_id, value_count, status,
                            first_seen_at, last_seen_at, tombstone_at,
                            collection_job_id
                        )
                        VALUES($1,1,'active',CURRENT_TIMESTAMP,
                               CURRENT_TIMESTAMP,NULL,$2)
                        ON CONFLICT(value_index_id) DO UPDATE SET
                            value_count=nl2sql_field_value_runtime_states.value_count + 1,
                            status='active',
                            last_seen_at=CURRENT_TIMESTAMP,
                            tombstone_at=NULL,
                            collection_job_id=EXCLUDED.collection_job_id
                        """,
                        value_index_id,
                        update.collection_job_id,
                    )
                    applied = True
                if annotation is not None:
                    annotation.value_index_id = value_index_id
                    await connection.execute(
                        f"""
                        INSERT INTO {self._table('nl2sql_field_value_annotations')}(
                            value_index_id, collection_job_id, database_id,
                            schema_name, raw_table_name, column_name, raw_value,
                            should_index, confidence, aliases, reason,
                            model_name, prompt_version, input_hash,
                            review_status, updated_at
                        )
                        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,
                               $12,$13,$14,$15,CURRENT_TIMESTAMP)
                        ON CONFLICT(collection_job_id, input_hash)
                        DO UPDATE SET
                            value_index_id=EXCLUDED.value_index_id,
                            should_index=EXCLUDED.should_index,
                            confidence=EXCLUDED.confidence,
                            aliases=EXCLUDED.aliases,
                            reason=EXCLUDED.reason,
                            model_name=EXCLUDED.model_name,
                            prompt_version=EXCLUDED.prompt_version,
                            review_status=EXCLUDED.review_status,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        annotation.value_index_id,
                        annotation.collection_job_id,
                        annotation.database_id,
                        annotation.schema_name,
                        annotation.raw_table_name,
                        annotation.column_name,
                        annotation.raw_value,
                        annotation.should_index,
                        annotation.confidence,
                        _json(annotation.aliases),
                        annotation.reason,
                        annotation.model_name,
                        annotation.prompt_version,
                        annotation.input_hash,
                        annotation.review_status,
                    )
                await connection.execute(
                    f"""
                    INSERT INTO {self._table('nl2sql_field_value_cdc_field_positions')}(
                        database_id, schema_name, raw_table_name, column_name,
                        source_position, source_order, updated_at
                    )
                    VALUES($1,$2,$3,$4,$5,$6,CURRENT_TIMESTAMP)
                    ON CONFLICT(
                        database_id, schema_name, raw_table_name, column_name
                    )
                    DO UPDATE SET
                        source_position=EXCLUDED.source_position,
                        source_order=EXCLUDED.source_order,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    update.database_id,
                    update.schema_name,
                    update.raw_table_name,
                    update.column_name,
                    update.source_position,
                    update.source_order,
                )
            status = "applied" if applied else "ignored"
            await connection.execute(
                f"""
                INSERT INTO {self._table('nl2sql_field_value_cdc_events')}(
                    event_id, database_id, event_json,
                    source_position, source_order, status, error_message,
                    transport_name, transport_partition,
                    transport_position, processed_at
                )
                VALUES($1,$2,$3::jsonb,$4,$5,$6,'',$7,$8,$9,
                       CURRENT_TIMESTAMP)
                """,
                message.event.event_id,
                message.event.database_id,
                message.event.model_dump_json(),
                message.event.source_position,
                message.event.source_order,
                status,
                message.transport_name,
                message.transport_partition,
                message.transport_position,
            )
            await connection.execute(
                f"""
                INSERT INTO {self._table('nl2sql_field_value_cdc_transport_positions')}(
                    database_id, transport_name, transport_partition,
                    transport_position, source_position, source_order,
                    updated_at
                )
                VALUES($1,$2,$3,$4,$5,$6,CURRENT_TIMESTAMP)
                ON CONFLICT(database_id, transport_name, transport_partition)
                DO UPDATE SET
                    transport_position=EXCLUDED.transport_position,
                    source_position=EXCLUDED.source_position,
                    source_order=EXCLUDED.source_order,
                    updated_at=CURRENT_TIMESTAMP
                """,
                message.event.database_id,
                message.transport_name,
                message.transport_partition,
                message.transport_position,
                message.event.source_position,
                message.event.source_order,
            )
            return status

    async def search_values(self, query: str, *, table_name: str | None = None, limit: int = 20):
        connection = await self._connect()
        trigram_table = (
            " OR (raw_value || ' ' || retrieval_text || ' ' || column_name) % $2"
            if self._trgm_enabled
            else ""
        )
        trigram_global = (
            " OR (raw_value || ' ' || retrieval_text || ' ' || column_name) % $1"
            if self._trgm_enabled
            else ""
        )
        if table_name:
            rows = await connection.fetch(
                f"""
                SELECT value.*,
                  ts_rank_cd(
                    to_tsvector(
                      'simple',
                      value.retrieval_text || ' ' || value.raw_value || ' ' ||
                      value.column_name
                    ),
                    plainto_tsquery('simple', $2)
                  )
                  {(" + similarity(value.raw_value || ' ' || value.retrieval_text, $2)" if self._trgm_enabled else "")}
                  AS score
                FROM {self._table('nl2sql_field_value_indexs')} value
                JOIN {self._table('nl2sql_field_value_runtime_states')} state
                  ON state.value_index_id=value.id
                WHERE state.status='active'
                  AND (
                    value.raw_table_name=$1
                    OR value.random_table_name=$1
                  )
                  AND (
                    to_tsvector(
                      'simple',
                      value.retrieval_text || ' ' || value.raw_value || ' ' ||
                      value.column_name
                    )
                    @@ plainto_tsquery('simple', $2)
                    {trigram_table}
                  )
                ORDER BY score DESC, value.id
                LIMIT $3
                """,
                table_name,
                query,
                max(1, int(limit)),
            )
        else:
            rows = await connection.fetch(
                f"""
                SELECT value.*,
                  ts_rank_cd(
                    to_tsvector(
                      'simple',
                      value.retrieval_text || ' ' || value.raw_value || ' ' ||
                      value.column_name
                    ),
                    plainto_tsquery('simple', $1)
                  )
                  {(" + similarity(value.raw_value || ' ' || value.retrieval_text, $1)" if self._trgm_enabled else "")}
                  AS score
                FROM {self._table('nl2sql_field_value_indexs')} value
                JOIN {self._table('nl2sql_field_value_runtime_states')} state
                  ON state.value_index_id=value.id
                WHERE state.status='active'
                  AND (
                    to_tsvector(
                      'simple',
                      value.retrieval_text || ' ' || value.raw_value || ' ' ||
                      value.column_name
                    )
                    @@ plainto_tsquery('simple', $1)
                    {trigram_global}
                  )
                ORDER BY score DESC, value.id
                LIMIT $2
                """,
                query,
                max(1, int(limit)),
            )
        return [(_value_from_row(row), float(row["score"] or 0.0)) for row in rows]

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
        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT value_index_id, value_count
            FROM {self._table('nl2sql_field_value_runtime_states')}
            WHERE value_index_id=ANY($1::bigint[])
              AND status='active'
            """,
            [int(value) for value in value_index_ids],
        )
        return {
            int(row["value_index_id"]): int(row["value_count"] or 0)
            for row in rows
        }

    async def search_evidence(self, query: str, limit: int = 20):
        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT *,
              ts_rank_cd(
                to_tsvector('simple', retrieval_text || ' ' || evidence_content),
                plainto_tsquery('simple', $1)
              )
              {(" + similarity(retrieval_text || ' ' || evidence_content, $1)" if self._trgm_enabled else "")}
              AS score
            FROM {self._table('nl2sql_evidence_indexs')}
            WHERE status=1
              AND (
                to_tsvector('simple', retrieval_text || ' ' || evidence_content)
                @@ plainto_tsquery('simple', $1)
                {(" OR (retrieval_text || ' ' || evidence_content) % $1" if self._trgm_enabled else "")}
              )
            ORDER BY score DESC, id
            LIMIT $2
            """,
            query,
            max(1, int(limit)),
        )
        return [(_evidence_from_row(row), float(row["score"] or 0.0)) for row in rows]

    async def list_evidence(self, limit: int = 1000):
        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT * FROM {self._table('nl2sql_evidence_indexs')}
            WHERE status=1 ORDER BY id LIMIT $1
            """,
            max(1, int(limit)),
        )
        return [_evidence_from_row(row) for row in rows]

    async def add_evidence_candidate(self, candidate: SQLEvidenceCandidate) -> int:
        connection = await self._connect()
        row = await connection.fetchrow(
            f"""
            INSERT INTO {self._table('nl2sql_evidence_candidate')}(
                source_query, candidate_trigger, candidate_evidence,
                confidence, status
            )
            VALUES($1,$2,$3,$4,$5)
            RETURNING id
            """,
            candidate.source_query,
            candidate.candidate_trigger,
            candidate.candidate_evidence,
            candidate.confidence,
            candidate.status,
        )
        return int(row["id"])

    async def list_evidence_candidates(self, *, status: str = "pending", limit: int = 100):
        connection = await self._connect()
        rows = await connection.fetch(
            f"""
            SELECT * FROM {self._table('nl2sql_evidence_candidate')}
            WHERE status=$1 ORDER BY id LIMIT $2
            """,
            status,
            max(1, int(limit)),
        )
        return [
            SQLEvidenceCandidate(
                id=int(row["id"]),
                source_query=row["source_query"],
                candidate_trigger=row["candidate_trigger"],
                candidate_evidence=row["candidate_evidence"],
                confidence=float(row["confidence"]),
                status=row["status"],
                created_at=str(row["created_at"]) if row["created_at"] else None,
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
    ):
        if status not in {"approved", "rejected", "disabled"}:
            raise ValueError("unsupported evidence candidate status")
        connection = await self._connect()
        row = await connection.fetchrow(
            f"""
            SELECT * FROM {self._table('nl2sql_evidence_candidate')}
            WHERE id=$1
            """,
            candidate_id,
        )
        if not row:
            return None
        await connection.execute(
            f"""
            UPDATE {self._table('nl2sql_evidence_candidate')}
            SET status=$1
            WHERE id=$2
            """,
            status,
            candidate_id,
        )
        if status != "approved":
            return None
        evidence_row = await connection.fetchrow(
            f"""
            INSERT INTO {self._table('nl2sql_evidence_indexs')}(
                triggers_json, retrieval_text, evidence_content,
                evidence_type, description, status
            )
            VALUES($1::jsonb,$2,$3,$4,$5,1)
            RETURNING *
            """,
            _json([row["candidate_trigger"]]),
            row["candidate_trigger"],
            row["candidate_evidence"],
            evidence_type,
            description,
        )
        return _evidence_from_row(evidence_row)

    async def list_graph_edges(
        self,
        table_name: str | None = None,
        *,
        include_candidates: bool = True,
        limit: int = 200,
    ):
        connection = await self._connect()
        clauses = ["status='active'"]
        args: list[Any] = []
        if not include_candidates:
            clauses.append("relation_type <> 'candidate'")
        if table_name:
            args.extend([table_name] * 4)
            clauses.append(
                "(raw_table_name=$1 OR random_table_name=$2 OR "
                "related_raw_table_name=$3 OR related_random_table_name=$4)"
            )
        limit_index = len(args) + 1
        args.append(max(1, int(limit)))
        rows = await connection.fetch(
            f"""
            SELECT * FROM {self._table('nl2sql_graph_indexs')}
            WHERE {' AND '.join(clauses)}
            ORDER BY relation_type, confidence DESC, id
            LIMIT ${limit_index}
            """,
            *args,
        )
        return [_edge_from_row(row) for row in rows]

    async def graph_neighbors(
        self,
        table_name: str,
        *,
        max_hops: int = 2,
        include_candidates: bool = True,
        limit: int = 100,
    ):
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
            adjacency[right].append(
                edge.model_copy(
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
            )
        start_keys = {
            key
            for key in adjacency
            if table_name in key
            or key.endswith(f".{table_name}")
            or any(
                table_name
                in {
                    edge.random_table_name,
                    edge.related_random_table_name,
                }
                for edge in adjacency[key]
            )
        }
        queue = deque((key, 0) for key in start_keys or {table_name})
        visited = set(start_keys)
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

    async def record_feedback(self, feedback: SQLFeedback):
        connection = await self._connect()
        await connection.execute(
            f"""
            INSERT INTO {self._table('nl2sql_feedback')}(
                source_query, generated_sql, execution_result,
                used_tables_json, join_conditions_json
            )
            VALUES($1,$2,$3,$4::jsonb,$5::jsonb)
            """,
            feedback.query,
            feedback.generated_sql,
            feedback.execution_result,
            _json(feedback.used_tables),
            _json(feedback.join_conditions),
        )
        candidate_evidence = feedback.candidate_evidence or feedback.evidence_content
        if candidate_evidence:
            await connection.execute(
                f"""
                INSERT INTO {self._table('nl2sql_evidence_candidate')}(
                    source_query, candidate_trigger, candidate_evidence,
                    confidence, status
                )
                VALUES($1,$2,$3,$4,'pending')
                """,
                feedback.query,
                feedback.candidate_trigger or feedback.query,
                candidate_evidence,
                max(0.0, min(1.0, feedback.confidence)),
            )
        tables = []
        for name in feedback.used_tables:
            table = await self.get_table(name)
            if table and all(
                table.raw_table_name != existing.raw_table_name for existing in tables
            ):
                tables.append(table)
        candidates = []
        conditions = _parse_join_conditions(feedback.join_conditions)
        for index, left in enumerate(tables):
            for right in tables[index + 1 :]:
                left_column, right_column = _resolve_pair_columns(
                    conditions.get(
                        frozenset({left.raw_table_name, right.raw_table_name}),
                        (None, None),
                    ),
                    left,
                    right,
                )
                edge = SQLGraphEdge(
                    db_type=left.db_type,
                    raw_schema_name=left.raw_schema_name,
                    related_raw_schema_name=right.raw_schema_name,
                    raw_table_name=left.raw_table_name,
                    random_table_name=left.random_table_name,
                    raw_column_name=left_column,
                    random_column_name=_random_column(left, left_column),
                    related_raw_table_name=right.raw_table_name,
                    related_random_table_name=right.random_table_name,
                    related_raw_column_name=right_column,
                    related_random_column_name=_random_column(right, right_column),
                    relation_type="candidate",
                    join_condition=(
                        f"{left.raw_table_name}.{left_column} = "
                        f"{right.raw_table_name}.{right_column}"
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
                )
                existing = await connection.fetchrow(
                    f"""
                    SELECT id, relation_type
                    FROM {self._table('nl2sql_graph_indexs')}
                    WHERE db_type=$1
                      AND (
                        (
                          raw_schema_name=$2 AND raw_table_name=$3
                          AND raw_column_name IS NOT DISTINCT FROM $4
                          AND related_raw_schema_name=$5
                          AND related_raw_table_name=$6
                          AND related_raw_column_name IS NOT DISTINCT FROM $7
                        )
                        OR
                        (
                          raw_schema_name=$5 AND raw_table_name=$6
                          AND raw_column_name IS NOT DISTINCT FROM $7
                          AND related_raw_schema_name=$2
                          AND related_raw_table_name=$3
                          AND related_raw_column_name IS NOT DISTINCT FROM $4
                        )
                      )
                    ORDER BY CASE relation_type
                        WHEN 'physical' THEN 1
                        WHEN 'validated' THEN 2
                        ELSE 3
                    END
                    LIMIT 1
                    """,
                    edge.db_type,
                    edge.raw_schema_name,
                    edge.raw_table_name,
                    edge.raw_column_name,
                    edge.related_raw_schema_name,
                    edge.related_raw_table_name,
                    edge.related_raw_column_name,
                )
                if existing and existing["relation_type"] in {"physical", "validated"}:
                    continue
                await self._upsert_edge(connection, edge)
                candidates.append(edge)
        return candidates

    async def update_graph_status(self, edge_id: int, *, relation_type: str, status: str):
        if relation_type not in {"physical", "candidate", "validated"}:
            raise ValueError("unsupported graph relation_type")
        if status not in {"active", "disabled"}:
            raise ValueError("unsupported graph status")
        connection = await self._connect()
        await connection.execute(
            f"""
            UPDATE {self._table('nl2sql_graph_indexs')}
            SET relation_type=$1, status=$2, updated_at=CURRENT_TIMESTAMP
            WHERE id=$3
            """,
            relation_type,
            status,
            edge_id,
        )

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None


def _safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise ValueError(f"invalid PostgreSQL identifier: {value!r}")
    return value


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, (list, tuple)):
        value = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in value
        ]
    return json.dumps(value, ensure_ascii=False, default=str)


def _terms(value: str) -> list[str]:
    return [
        term.lower()
        for term in re.findall(r"[\w\u4e00-\u9fff]+", value or "")
        if term.strip()
    ]


def _vector_literal(values: Sequence[float] | None) -> str | None:
    if values is None:
        return None
    return "[" + ",".join(f"{float(value):.10g}" for value in values) + "]"


def _table_from_row(row) -> SQLTableSchema:
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
        columns=_json_value(row["columns_json"]),
        foreign_keys=_json_value(row["foreign_keys_json"]),
        indexes=_json_value(row["indexes_json"]),
        row_count=row["row_count"],
        sample_rows=_json_value(row["sample_rows_json"]),
        qa_text=row["qa_text"] or "",
        created_at=str(row["created_at"]) if row["created_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


def _value_from_row(row) -> SQLFieldValue:
    return SQLFieldValue(
        id=int(row["id"]),
        database_id=int(row["database_id"]),
        schema_name=row["schema_name"],
        raw_value=row["raw_value"],
        raw_table_name=row["raw_table_name"],
        random_table_name=row["random_table_name"],
        column_name=row["column_name"],
        aliases=_json_value(row["aliases"]),
        retrieval_text=row["retrieval_text"],
        created_at=str(row["created_at"]) if row["created_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


def _annotation_from_row(row) -> SQLFieldValueAnnotation:
    """把 PostgreSQL 行转换为字段值标注对象。

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
        aliases=_json_value(row["aliases"]),
        reason=str(row["reason"]),
        model_name=str(row["model_name"]),
        prompt_version=str(row["prompt_version"]),
        input_hash=str(row["input_hash"]),
        review_status=str(row["review_status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _evidence_from_row(row) -> SQLEvidence:
    return SQLEvidence(
        id=int(row["id"]),
        triggers=_json_value(row["triggers_json"]),
        retrieval_text=row["retrieval_text"],
        evidence_content=row["evidence_content"],
        evidence_type=row["evidence_type"],
        description=row["description"],
        status=int(row["status"]),
        created_at=str(row["created_at"]) if row["created_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


def _edge_from_row(row) -> SQLGraphEdge:
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
        created_at=str(row["created_at"]) if row["created_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


def _json_value(value):
    """Decode JSONB values returned as text by asyncpg."""

    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
