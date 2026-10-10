"""字段值 CDC 的发布、消费和周期全量校准运行时。"""

from __future__ import annotations

import asyncio
import time

from ..indexing.field_value_index_rules import (
    get_field_value_rejection_reason,
)
from ..indexing.field_value_llm_annotation import SQLFieldValueLLMAnnotator
from ..vssag_schemas.sql import (
    SQLCollectedFieldValue,
    SQLFieldValue,
    SQLFieldValueCDCUpdate,
)


class SQLFieldValueCDCRuntime:
    """连接真实 CDC 源、消息传输和索引库事务。"""

    def __init__(
        self,
        store,
        transport,
        field_value_config,
        *,
        llm=None,
        model_name: str = "unknown",
        allow_real_table_names: bool = True,
        embedder=None,
        embedding_model_name: str = "",
    ):
        self.store = store
        self.transport = transport
        self.field_value_config = field_value_config
        self.config = field_value_config.cdc
        self.recalibration_required = False
        self._last_recalibration = time.monotonic()
        self.embedder = embedder
        self.embedding_model_name = embedding_model_name or "default"
        self.annotator = (
            SQLFieldValueLLMAnnotator(
                llm,
                store,
                field_value_config.llm_annotation,
                model_name=model_name,
                allow_real_table_names=allow_real_table_names,
            )
            if field_value_config.llm_annotation.enabled and llm is not None
            else None
        )

    async def publish_once(
        self,
        source,
        *,
        database_id: int,
        db_type: str,
        source_name: str,
    ) -> int:
        """读取并发布一批 WAL/Binlog 事件。

        Args:
            source: PostgreSQL WAL 或 MySQL Binlog 源。
            database_id: 目标数据库 ID。
            db_type: PostgreSQL 或 MySQL。
            source_name: 复制槽或 Binlog 读取器名称。

        Returns:
            成功发布的事件数量。
        """

        indexed_tables = await self.store.list_cdc_indexed_tables(database_id)
        initial_position = await source.prepare(indexed_tables)
        position = await self.store.get_cdc_source_position(
            database_id=database_id,
            db_type=db_type,
            source_name=source_name,
        )
        if not position:
            position = initial_position
            await self.store.save_cdc_source_position(
                database_id=database_id,
                db_type=db_type,
                source_name=source_name,
                source_position=position,
            )
        events, last_position = await source.read_batch(
            position,
            self.config.batch_max_events,
        )
        if events:
            await self.transport.publish(events)
        if last_position and last_position != position:
            await source.confirm(last_position)
            await self.store.save_cdc_source_position(
                database_id=database_id,
                db_type=db_type,
                source_name=source_name,
                source_position=last_position,
            )
        return len(events)

    async def consume_once(self) -> dict[str, int]:
        """消费一批消息，执行幂等、乱序判断和引用计数更新。

        Returns:
            本批接收、成功、重复/忽略和失败数量。
        """

        messages = await self.transport.consume_batch(
            max_events=self.config.batch_max_events,
            timeout_seconds=self.config.batch_window_seconds,
        )
        acknowledged = []
        result = {
            "received": len(messages),
            "applied": 0,
            "ignored": 0,
            "failed": 0,
        }
        for message in messages:
            try:
                if message.event.operation == "schema":
                    self.recalibration_required = True
                updates: list[SQLFieldValueCDCUpdate] = []
                column_names = sorted(
                    set(message.event.old_values)
                    | set(message.event.new_values)
                )
                for column_name in column_names:
                    raw_old = message.event.old_values.get(column_name)
                    raw_new = message.event.new_values.get(column_name)
                    if raw_old == raw_new:
                        continue
                    context = await self.store.get_cdc_field_context(
                        database_id=message.event.database_id,
                        schema_name=message.event.schema_name,
                        raw_table_name=message.event.raw_table_name,
                        column_name=column_name,
                    )
                    if context is None:
                        continue
                    statistics, random_table_name = context
                    if statistics.selection_decision not in {
                        "manual",
                        "candidate",
                        "llm_reviewed",
                        "uncertain",
                    }:
                        continue
                    old_value = str(raw_old) if raw_old is not None else None
                    new_value = str(raw_new) if raw_new is not None else None
                    if old_value is not None and get_field_value_rejection_reason(
                        old_value,
                        max_length=self.field_value_config.max_value_length,
                        hard_reject_sensitive=(
                            self.field_value_config.hard_reject_sensitive
                        ),
                        patterns=self.field_value_config.value_patterns,
                        column_name=column_name,
                    ):
                        old_value = None
                    if new_value is not None and get_field_value_rejection_reason(
                        new_value,
                        max_length=self.field_value_config.max_value_length,
                        hard_reject_sensitive=(
                            self.field_value_config.hard_reject_sensitive
                        ),
                        patterns=self.field_value_config.value_patterns,
                        column_name=column_name,
                    ):
                        new_value = None
                    annotation = None
                    if (
                        new_value is not None
                        and statistics.selection_decision
                        in {"llm_reviewed", "uncertain"}
                    ):
                        if self.annotator is None:
                            self.recalibration_required = True
                            continue
                        candidates = await self.annotator.annotate_page(
                            statistics,
                            [
                                SQLCollectedFieldValue(
                                    value=SQLFieldValue(
                                        database_id=message.event.database_id,
                                        schema_name=message.event.schema_name,
                                        raw_value=new_value,
                                        raw_table_name=(
                                            message.event.raw_table_name
                                        ),
                                        random_table_name=random_table_name,
                                        column_name=column_name,
                                        aliases=[],
                                        retrieval_text=new_value,
                                    ),
                                    value_count=1,
                                )
                            ],
                            random_table_name=random_table_name,
                        )
                        annotation = candidates[0].annotation
                        if annotation is None:
                            raise RuntimeError("CDC LLM 标注未返回标注记录")
                        if not annotation.should_index:
                            new_value = None
                        if annotation.review_status == "pending":
                            self.recalibration_required = True
                    if old_value is None and new_value is None and annotation is None:
                        continue
                    updates.append(
                        SQLFieldValueCDCUpdate(
                            event_id=message.event.event_id,
                            collection_job_id=statistics.collection_job_id,
                            database_id=message.event.database_id,
                            schema_name=message.event.schema_name,
                            raw_table_name=message.event.raw_table_name,
                            random_table_name=random_table_name,
                            column_name=column_name,
                            old_value=old_value,
                            new_value=new_value,
                            source_position=message.event.source_position,
                            source_order=message.event.source_order,
                            annotation=annotation,
                        )
                    )
                status = await self.store.apply_cdc_event(message, updates)
                if self.embedder is not None and status in {
                    "applied",
                    "duplicate",
                }:
                    changed_values = [
                        SQLFieldValue(
                            database_id=update.database_id,
                            schema_name=update.schema_name,
                            raw_value=update.new_value,
                            raw_table_name=update.raw_table_name,
                            random_table_name=update.random_table_name,
                            column_name=update.column_name,
                            aliases=(
                                list(update.annotation.aliases)
                                if update.annotation is not None
                                else []
                            ),
                            retrieval_text=" ".join(
                                [
                                    update.new_value,
                                    *(
                                        update.annotation.aliases
                                        if update.annotation is not None
                                        else []
                                    ),
                                ]
                            ).strip(),
                        )
                        for update in updates
                        if update.new_value is not None
                        and (
                            update.annotation is None
                            or update.annotation.should_index
                        )
                    ]
                    filter_values = getattr(
                        self.store,
                        "filter_field_values_requiring_embeddings",
                        None,
                    )
                    if filter_values is not None:
                        changed_values = await filter_values(
                            changed_values,
                            model_name=self.embedding_model_name,
                        )
                    if changed_values:
                        vectors = await self.embedder.embed(
                            [
                                value.retrieval_text or value.raw_value
                                for value in changed_values
                            ]
                        )
                        await self.store.upsert_embeddings(
                            values=list(
                                zip(changed_values, vectors, strict=True)
                            ),
                            model_name=self.embedding_model_name,
                        )
                if status == "applied":
                    result["applied"] += 1
                else:
                    result["ignored"] += 1
                acknowledged.append(message)
            except Exception as exc:
                result["failed"] += 1
                backoff_seconds = float(
                    self.config.retry_backoff_seconds
                ) * max(1, int(message.attempt) + 1)
                if backoff_seconds > 0:
                    await asyncio.sleep(min(backoff_seconds, 30.0))
                should_ack = await self.transport.retry(
                    message,
                    str(exc),
                    max_retries=self.config.max_retries,
                )
                if should_ack:
                    acknowledged.append(message)
        await self.transport.ack(acknowledged)
        return result

    async def run_once(
        self,
        source,
        *,
        database_id: int,
        db_type: str,
        source_name: str,
    ) -> dict[str, int]:
        """执行一次源端发布和消费。

        Args:
            source: PostgreSQL WAL 或 MySQL Binlog 源。
            database_id: 目标数据库 ID。
            db_type: PostgreSQL 或 MySQL。
            source_name: 复制槽或 Binlog 读取器名称。

        Returns:
            发布数量和消费统计。
        """

        published = await self.publish_once(
            source,
            database_id=database_id,
            db_type=db_type,
            source_name=source_name,
        )
        result = await self.consume_once()
        result["published"] = published
        return result

    async def collect_and_replay(
        self,
        source,
        index_pipeline,
        *,
        database_id: int,
        db_type: str,
        source_name: str,
    ):
        """记录源位点后执行全量采集，并排空扫描期间的增量事件。

        Args:
            source: PostgreSQL WAL 或 MySQL Binlog 源。
            index_pipeline: SQL Schema 和字段值全量采集 Pipeline。
            database_id: 目标数据库 ID。
            db_type: PostgreSQL 或 MySQL。
            source_name: 复制槽或 Binlog 读取器名称。

        Returns:
            全量采集结果和增量发布、消费统计。

        Raises:
            RuntimeError: 采集结果没有字段值采集任务 ID。
        """

        while True:
            published = await self.publish_once(
                source,
                database_id=database_id,
                db_type=db_type,
                source_name=source_name,
            )
            consumed = await self.consume_once()
            if published == 0 and consumed["received"] == 0:
                break

        indexed_tables = await self.store.list_cdc_indexed_tables(database_id)
        await source.prepare(indexed_tables)
        start_position = await source.capture_current_position()
        collection = await index_pipeline.collect(
            cdc_start_position=start_position
        )
        if not collection.collection_job_id:
            raise RuntimeError("字段值全量采集结果缺少 collection_job_id")

        indexed_tables = await self.store.list_cdc_indexed_tables(database_id)
        await source.prepare(indexed_tables)
        await source.confirm(start_position)
        await self.store.save_cdc_source_position(
            database_id=database_id,
            db_type=db_type,
            source_name=source_name,
            source_position=start_position,
        )
        result = {
            "published": 0,
            "received": 0,
            "applied": 0,
            "ignored": 0,
            "failed": 0,
        }
        while True:
            published = await self.publish_once(
                source,
                database_id=database_id,
                db_type=db_type,
                source_name=source_name,
            )
            consumed = await self.consume_once()
            result["published"] += published
            for key in ("received", "applied", "ignored", "failed"):
                result[key] += consumed[key]
            if published == 0 and consumed["received"] == 0:
                break

        end_position = await self.store.get_cdc_source_position(
            database_id=database_id,
            db_type=db_type,
            source_name=source_name,
        )
        await self.store.save_collection_cdc_positions(
            collection_job_id=collection.collection_job_id,
            start_position=start_position,
            end_position=end_position,
        )
        return collection, result

    async def replay_failed_messages(self, *, max_events: int = 100) -> int:
        """把 DLQ 中的失败事件重新发布到主消息流。

        Args:
            max_events: 本次最多重放的消息数量。

        Returns:
            已成功重新发布的消息数量。

        Raises:
            RuntimeError: 当前消息传输未实现 DLQ 重放。
        """

        replay = getattr(self.transport, "replay_dlq", None)
        if replay is None:
            raise RuntimeError("当前 CDC 消息传输不支持 DLQ 重放")
        return await replay(max_events=max(1, int(max_events)))

    async def run_forever(
        self,
        source,
        index_pipeline,
        *,
        database_id: int,
        db_type: str,
        source_name: str,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """持续运行 CDC，并按配置或 Schema 事件执行全量校准。

        Args:
            source: PostgreSQL WAL 或 MySQL Binlog 源。
            index_pipeline: 用于周期全量校准的 SQL Schema Pipeline。
            database_id: 目标数据库 ID。
            db_type: PostgreSQL 或 MySQL。
            source_name: 复制槽或 Binlog 读取器名称。
            stop_event: 外部优雅停止事件。
        """

        await self.collect_and_replay(
            source,
            index_pipeline,
            database_id=database_id,
            db_type=db_type,
            source_name=source_name,
        )

        while stop_event is None or not stop_event.is_set():
            await self.run_once(
                source,
                database_id=database_id,
                db_type=db_type,
                source_name=source_name,
            )
            now = time.monotonic()
            if (
                self.recalibration_required
                or now - self._last_recalibration
                >= self.config.recalibration_interval_seconds
            ):
                await self.collect_and_replay(
                    source,
                    index_pipeline,
                    database_id=database_id,
                    db_type=db_type,
                    source_name=source_name,
                )
                self.recalibration_required = False
                self._last_recalibration = time.monotonic()
            wait_seconds = min(
                5.0,
                max(
                    0.1,
                    float(
                        getattr(
                            source.config,
                            "poll_interval_seconds",
                            self.config.batch_window_seconds,
                        )
                    ),
                ),
            )
            if stop_event is None:
                await asyncio.sleep(wait_seconds)
            else:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=wait_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
