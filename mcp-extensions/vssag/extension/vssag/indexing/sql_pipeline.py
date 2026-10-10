"""SQL Schema 分阶段采集与索引流水线。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from ..config import SQLAbilityConfig
from ..errors import ConfigurationError
from ..providers.sql_prov.base import SQLSchemaProvider
from ..vssag_schemas.sql import (
    SQLFeedback,
    SQLFieldValueCollectionJob,
    SQLSchemaCollection,
)
from .field_value_llm_annotation import SQLFieldValueLLMAnnotator
from .index_enrichment.graph import extract_business_graph
from .index_enrichment.qa_gen import generate_table_qa
from .index_enrichment.sql_ddl import enrich_collection

SQLIndexStrategy = Literal["fast", "standard", "full"]


class SQLCollectionPaused(Exception):
    """表示 SQL 采集已在 checkpoint 边界暂停。"""


class SQLCollectionCancelled(Exception):
    """表示 SQL 采集已被管理员取消。"""


class SQLSchemaIndexPipeline:
    def __init__(
        self,
        provider: SQLSchemaProvider,
        store,
        config: SQLAbilityConfig,
        *,
        llm=None,
        embedder=None,
    ):
        self.provider = provider
        self.store = store
        self.config = config
        self.llm = llm
        embedding = config.retriever_service.index.embedding
        self.embedder = embedder
        if embedding.enabled and self.embedder is None:
            raise ConfigurationError(
                "SQL embedding indexing requires an injected Embedder"
            )
        self.graph_trace: list[dict] = []

    async def collect(
        self,
        *,
        cdc_start_position: str = "",
        strategy: SQLIndexStrategy = "standard",
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
        control_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> SQLSchemaCollection:
        """按策略分阶段采集 SQL Schema、字段值并构建索引。

        Args:
            cdc_start_position: 全量扫描开始前记录的 CDC 源位置。
            strategy: 索引策略，支持 fast、standard、full。
            progress_callback: 阶段和分页进度回调。
            control_callback: 管理员控制回调，返回 continue、pause 或 cancel。

        Returns:
            完整的 SQL Schema 采集结果。

        Raises:
            ValueError: 策略名称不受支持。
            SQLCollectionPaused: 任务在 checkpoint 边界暂停。
            SQLCollectionCancelled: 任务被取消。
        """

        if strategy not in {"fast", "standard", "full"}:
            raise ValueError(f"不支持的 SQL 索引策略: {strategy}")

        async def report(
            stage: str,
            progress: float,
            message: str,
            **extra: Any,
        ) -> None:
            event = {
                "sdk": "vssag",
                "stage": stage,
                "progress": progress,
                "message": message,
                **extra,
            }
            if progress_callback is not None:
                result = progress_callback(event)
                if inspect.isawaitable(result):
                    await result

        target = self.config.target_sources
        field_value_config = target.field_value_index
        collection_job = None
        find_resumable_job = getattr(
            self.store,
            "find_resumable_collection_job",
            None,
        )
        if find_resumable_job is not None:
            collection_job = await find_resumable_job(
                database_id=field_value_config.database_id,
                db_type=self.provider.db_type,
                mode=field_value_config.mode,
            )
        if collection_job is None:
            collection_job = SQLFieldValueCollectionJob(
                job_id=uuid4().hex,
                database_id=field_value_config.database_id,
                db_type=self.provider.db_type,
                mode=field_value_config.mode,
            )
        else:
            collection_job.status = "running"
            collection_job.finished_at = None
            collection_job.error_message = ""
        if cdc_start_position:
            collection_job.cdc_start_position = cdc_start_position

        async def finish(status_value: str, error_message: str = "") -> None:
            collection_job.status = status_value
            collection_job.finished_at = datetime.now(timezone.utc)
            collection_job.error_message = error_message
            await self.store.complete_collection_job(collection_job)

        async def control(event: dict[str, Any]) -> None:
            event = {
                "sdk": "vssag",
                "collection_job_id": collection_job.job_id,
                **event,
            }
            is_cancelled = getattr(
                self.store,
                "is_collection_job_cancelled",
                None,
            )
            if is_cancelled is not None and await is_cancelled(collection_job.job_id):
                await finish("cancelled", "任务已取消")
                raise SQLCollectionCancelled("SQL Schema 采集任务已取消")
            if control_callback is None:
                return
            result = control_callback(event)
            if inspect.isawaitable(result):
                result = await result
            action = result.get("action") if isinstance(result, dict) else result
            for field_name in (
                "processed_tables",
                "processed_fields",
                "processed_values",
            ):
                if field_name in event:
                    setattr(collection_job, field_name, int(event[field_name] or 0))
            if action == "pause":
                await finish(
                    "paused",
                    "任务已暂停，已保存当前阶段进度和字段值 checkpoint",
                )
                raise SQLCollectionPaused("SQL Schema 采集任务已暂停")
            if action == "cancel":
                cancel = getattr(self.store, "cancel_collection_job", None)
                if cancel is not None:
                    await cancel(collection_job.job_id)
                else:
                    await finish("cancelled", "任务已取消")
                raise SQLCollectionCancelled("SQL Schema 采集任务已取消")

        await self.store.begin_collection_job(collection_job)
        await report(
            "prepare",
            0.02,
            "准备 SQL Schema 采集",
            collection_job_id=collection_job.job_id,
            strategy=strategy,
        )
        try:
            await control({"stage": "prepare", "progress": 0.02})
            checkpoints = []
            list_checkpoints = getattr(
                self.store,
                "list_collection_checkpoints",
                None,
            )
            if list_checkpoints is not None:
                checkpoints = await list_checkpoints(collection_job.job_id)
            persist_field_value_page = getattr(
                self.store,
                "save_collection_page",
                None,
            )
            annotate_field_value_page = None
            if (
                strategy in {"standard", "full"}
                and field_value_config.mode == "auto"
                and field_value_config.llm_annotation.enabled
            ):
                annotate_field_value_page = SQLFieldValueLLMAnnotator(
                    self.llm,
                    self.store,
                    field_value_config.llm_annotation,
                    model_name=getattr(self.llm, "model_name", "unknown"),
                    allow_real_table_names=(
                        self.config.security.allow_real_table_names
                    ),
                ).annotate_page

            await report("schema", 0.10, "正在采集目标数据库表结构")
            schema_collection = await self.provider.collect(
                exclude_schemas=target.exclude_schemas,
                exclude_tables=target.exclude_tables,
                value_columns=target.value_columns,
                sample_rows=target.sample_rows,
                max_values_per_column=target.max_values_per_column,
                database_id=field_value_config.database_id,
                collection_job_id=collection_job.job_id,
                field_value_config=field_value_config,
                collection_checkpoints=checkpoints,
                collect_field_values=False,
            )
            schema_collection = enrich_collection(
                schema_collection,
                allow_real_table_names=self.config.security.allow_real_table_names,
            )
            await control(
                {
                    "stage": "schema",
                    "progress": 0.28,
                    "processed_tables": len(schema_collection.tables),
                    "total_tables": len(schema_collection.tables),
                }
            )
            if strategy == "full":
                await report("enrich", 0.34, "正在执行 SQL Schema LLM 增强")
                schema_collection = await generate_table_qa(
                    schema_collection,
                    self.llm,
                    enabled=self.config.retriever_service.index.qa.enabled,
                )
                self.graph_trace = []
                schema_collection = await extract_business_graph(
                    schema_collection,
                    self.llm,
                    allow_real_table_names=self.config.security.allow_real_table_names,
                    enabled=self.config.retriever_service.index.graph.enabled,
                    trace=self.graph_trace,
                )

            get_hashes = getattr(self.store, "get_table_hashes", None)
            old_hashes = (
                await get_hashes(
                    schema_collection.db_type,
                    database_id=schema_collection.database_id,
                )
                if get_hashes is not None
                else {}
            )
            current_hashes = {
                (table.raw_schema_name, table.raw_table_name): table.raw_ddl_hash
                for table in schema_collection.tables
            }
            changed_tables = {
                key
                for key, ddl_hash in current_hashes.items()
                if old_hashes.get(key) != ddl_hash
            }
            removed_tables = set(old_hashes).difference(current_hashes)
            sync = getattr(self.store, "sync_collection", None)
            replace = getattr(self.store, "replace_collection", None)
            if sync is not None:
                await sync(
                    schema_collection,
                    changed_tables=changed_tables,
                    removed_tables=removed_tables,
                )
            elif replace is not None:
                await replace(schema_collection)
            else:
                await self.store.upsert_collection(schema_collection)
            await self.store.reconcile_collection_job(schema_collection)
            collection_job.processed_tables = len(schema_collection.tables)
            await report(
                "persist",
                0.42,
                "表结构已写入 SQL 索引，可立即检索",
                collection_job_id=collection_job.job_id,
                processed_tables=len(schema_collection.tables),
                total_tables=len(schema_collection.tables),
            )
            await control(
                {
                    "stage": "persist",
                    "progress": 0.42,
                    "processed_tables": len(schema_collection.tables),
                    "total_tables": len(schema_collection.tables),
                }
            )

            if strategy == "fast":
                await finish("completed")
                await report("completed", 1.0, "快速 SQL Schema 索引完成")
                return schema_collection

            await report(
                "table_embeddings",
                0.44,
                "正在构建表结构向量索引",
                collection_job_id=collection_job.job_id,
                processed_tables=len(schema_collection.tables),
                total_tables=len(schema_collection.tables),
            )
            await self._index_embeddings(
                schema_collection,
                changed_tables=changed_tables,
                include_tables=True,
                include_values=False,
                include_evidence=True,
                progress_callback=progress_callback,
                control_callback=control,
                stage="table_embeddings",
                progress_start=0.44,
                progress_end=0.48,
                collection_job_id=collection_job.job_id,
            )
            await control(
                {
                    "stage": "table_embeddings",
                    "progress": 0.48,
                    "processed_tables": len(schema_collection.tables),
                    "total_tables": len(schema_collection.tables),
                }
            )

            await report("field_values", 0.48, "正在按 checkpoint 采集字段值")
            field_collection = await self.provider.collect(
                exclude_schemas=target.exclude_schemas,
                exclude_tables=target.exclude_tables,
                value_columns=target.value_columns,
                sample_rows=target.sample_rows,
                max_values_per_column=target.max_values_per_column,
                database_id=field_value_config.database_id,
                collection_job_id=collection_job.job_id,
                field_value_config=field_value_config,
                collection_checkpoints=checkpoints,
                collect_field_values=True,
                table_schemas=schema_collection.tables,
                persist_field_value_page=persist_field_value_page,
                annotate_field_value_page=annotate_field_value_page,
                control_callback=control,
            )
            list_saved_values = getattr(
                self.store,
                "list_collection_field_values",
                None,
            )
            if persist_field_value_page is not None and list_saved_values is not None:
                field_collection.collected_field_values = await list_saved_values(
                    collection_job.job_id
                )
            collection = schema_collection.model_copy(
                update={
                    "tables": field_collection.tables,
                    "collected_field_values": field_collection.collected_field_values,
                    "field_statistics": field_collection.field_statistics,
                }
            )
            collection_job.processed_fields = len(collection.field_statistics)
            collection_job.processed_values = len(collection.collected_field_values)
            await control(
                {
                    "stage": "field_values",
                    "progress": 0.72,
                    "processed_tables": len(collection.tables),
                    "processed_fields": len(collection.field_statistics),
                    "processed_values": len(collection.collected_field_values),
                }
            )
            if sync is not None:
                # Schema 阶段已经处理表结构变化和删除；字段值阶段不能再次
                # 删除刚刚完成的表结构向量，只同步字段值和统计记录。
                await sync(
                    collection,
                    changed_tables=set(),
                    removed_tables=set(),
                )
            elif replace is not None:
                await replace(collection, changed_tables=set())
            else:
                await self.store.upsert_collection(collection)
            await self.store.reconcile_collection_job(collection)
            if strategy in {"standard", "full"}:
                await report(
                    "field_value_embeddings",
                    0.80,
                    "正在构建字段值向量索引",
                    collection_job_id=collection_job.job_id,
                    processed_values=len(collection.collected_field_values),
                )
                await self._index_embeddings(
                    collection,
                    changed_tables=changed_tables,
                    include_tables=False,
                    include_values=True,
                    include_evidence=False,
                    progress_callback=progress_callback,
                    control_callback=control,
                    stage="field_value_embeddings",
                    progress_start=0.80,
                    progress_end=0.98,
                    collection_job_id=collection_job.job_id,
                )
            await finish("completed")
            await report(
                "completed",
                1.0,
                "SQL Schema 采集和索引构建完成",
                collection_job_id=collection_job.job_id,
                processed_tables=len(collection.tables),
                processed_fields=len(collection.field_statistics),
                processed_values=len(collection.collected_field_values),
            )
            return collection
        except SQLCollectionPaused:
            raise
        except SQLCollectionCancelled:
            raise
        except asyncio.CancelledError:
            await finish("cancelled", "任务已取消")
            raise
        except Exception as exc:
            collection_job.status = "failed"
            collection_job.finished_at = datetime.now(timezone.utc)
            collection_job.error_count += 1
            collection_job.error_message = str(exc)
            await self.store.complete_collection_job(collection_job)
            raise

    async def index(self, collection: SQLSchemaCollection) -> dict[str, int]:
        """兼容旧调用，直接写入集合并构建向量索引。"""

        result = await self.store.upsert_collection(collection)
        await self._index_embeddings(collection)
        return result

    async def record_feedback(self, feedback: SQLFeedback):
        return await self.store.record_feedback(feedback)

    async def _index_embeddings(
        self,
        collection: SQLSchemaCollection,
        *,
        changed_tables: set[tuple[str, str]] | None = None,
        include_tables: bool = True,
        include_values: bool = True,
        include_evidence: bool = True,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
        control_callback: Callable[[dict[str, Any]], Any] | None = None,
        stage: str = "embedding",
        progress_start: float = 0.80,
        progress_end: float = 0.98,
        collection_job_id: str = "",
    ) -> None:
        embedding_config = self.config.retriever_service.index.embedding
        if not embedding_config.enabled or self.embedder is None:
            return
        if include_tables and changed_tables:
            table_keys = set(changed_tables)
            tables_to_index = [
                table
                for table in collection.tables
                if (table.raw_schema_name, table.raw_table_name) in table_keys
            ]
        elif include_tables:
            tables_to_index = collection.tables
        else:
            tables_to_index = []
        if include_values and changed_tables:
            raw_names = {
                table.raw_table_name
                for table in collection.tables
                if (table.raw_schema_name, table.raw_table_name)
                in set(changed_tables)
            }
            values_to_index = [
                item.value
                for item in collection.collected_field_values
                if item.value.raw_table_name in raw_names
            ]
        elif include_values:
            values_to_index = [
                item.value for item in collection.collected_field_values
            ]
        else:
            values_to_index = []
        filter_values = getattr(
            self.store,
            "filter_field_values_requiring_embeddings",
            None,
        )
        if include_values and filter_values is not None:
            values_to_index = await filter_values(
                values_to_index,
                model_name=embedding_config.model_name,
            )
        evidence_to_index = collection.evidence if include_evidence else []
        batch_size = 64
        total_items = max(
            len(tables_to_index),
            len(values_to_index),
            len(evidence_to_index),
            1,
        )
        batches = max(1, (total_items + batch_size - 1) // batch_size)
        batch_index = 0
        for offset in range(0, total_items, batch_size):
            table_batch = tables_to_index[offset : offset + batch_size]
            value_batch = values_to_index[offset : offset + batch_size]
            evidence_batch = evidence_to_index[offset : offset + batch_size]
            table_vectors = (
                await self.embedder.embed(
                    [table.random_schema_summary for table in table_batch]
                )
                if table_batch
                else []
            )
            value_vectors = (
                await self.embedder.embed(
                    [
                        value.retrieval_text or value.raw_value
                        for value in value_batch
                    ]
                )
                if value_batch
                else []
            )
            evidence_vectors = (
                await self.embedder.embed(
                    [
                        item.retrieval_text or item.evidence_content
                        for item in evidence_batch
                    ]
                )
                if evidence_batch
                else []
            )
            await self.store.upsert_embeddings(
                tables=list(zip(table_batch, table_vectors, strict=False)),
                values=list(zip(value_batch, value_vectors, strict=False)),
                evidence=list(zip(evidence_batch, evidence_vectors, strict=False)),
                model_name=embedding_config.model_name,
            )
            batch_index += 1
            event = {
                "sdk": "vssag",
                "stage": stage,
                "progress": min(
                    progress_end,
                    progress_start
                    + (progress_end - progress_start) * batch_index / batches,
                ),
                "message": "正在分批构建 SQL 向量索引",
                "collection_job_id": collection_job_id,
                "processed_batches": batch_index,
                "total_batches": batches,
            }
            if progress_callback is not None:
                progress_event = dict(event)
                result = progress_callback(progress_event)
                if inspect.isawaitable(result):
                    await result
            if control_callback is not None:
                result = control_callback(event)
                if inspect.isawaitable(result):
                    await result
