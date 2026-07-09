"""字段值索引同步服务。"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

from ....providers.normalizers import TextNormalizer
from ....providers.normalizers import DefaultTextNormalizer
from ....providers.alias import AliasProvider, EmptyAliasProvider
from ....configs import TableRAGConfig
from ....schemas import (
    FieldValueIndexTarget,
    FieldValueRecord,
    FieldValueSyncReport,
    TableValueIndexTarget,
    TableValueSyncReport,
    ValueIndexSyncReport,
)
from .base import ValueSourceReader, ValueIndexStore

log = logging.getLogger(__name__)


class SyncFieldValueIndexService:
    """字段值索引业务服务，编排源表读取、文本处理和索引写入。"""

    def __init__(
        self,
        config: TableRAGConfig,
        source_reader: ValueSourceReader,
        index_store: ValueIndexStore,
        normalizer: TextNormalizer | None = None,
        alias_provider: AliasProvider | None = None,
    ):
        """初始化字段值索引服务。

        Args:
            config: TableRAG 检索模块总配置。
            source_reader: 业务源表字段值读取器。
            index_store: 字段值索引存储。
            normalizer: 可选文本归一化器；为空时使用默认实现。
            alias_provider: 可选字段值别名提供器；为空时不返回别名。

        Returns:
            None。
        """
        self.config = config
        self.source_reader = source_reader
        self.index_store = index_store
        self.normalizer = normalizer or DefaultTextNormalizer()
        self.alias_provider = alias_provider or EmptyAliasProvider()

    def sync_all_report(self, *, continue_on_error: bool = True) -> ValueIndexSyncReport:
        """同步所有字段值索引并返回生产级报告。

        Args:
            continue_on_error: 单个表或字段失败后是否继续同步后续目标。

        Returns:
            包含表级、字段级数量、耗时、跳过原因和错误摘要的同步报告。
        """
        started_at = time.perf_counter()
        table_reports: list[TableValueSyncReport] = []
        errors: list[str] = []
        for target in self.config.field_value_sync.targets:
            if not target.enabled:
                table_reports.append(
                    TableValueSyncReport(
                        table_name=target.table_name,
                        status="skipped",
                        skip_reason="target_disabled",
                    )
                )
                continue
            try:
                table_report = self.sync_target_report(target, continue_on_error=continue_on_error)
                table_reports.append(table_report)
                if table_report.error:
                    errors.append(table_report.error)
                errors.extend(field.error for field in table_report.fields if field.error)
            except Exception as exc:
                if not continue_on_error:
                    raise
                error = f"{target.table_name}: {exc}"
                errors.append(error)
                table_reports.append(
                    TableValueSyncReport(
                        table_name=target.table_name,
                        status="error",
                        error=error,
                    )
                )
        total = sum(table.indexed_count for table in table_reports)
        status = "success"
        if errors:
            status = "partial_error" if total else "error"
        return ValueIndexSyncReport(
            status=status,
            total_indexed_count=total,
            duration_ms=_elapsed_ms(started_at),
            tables=table_reports,
            errors=errors,
        )

    def sync_target_report(
        self,
        target: TableValueIndexTarget,
        *,
        continue_on_error: bool = True,
    ) -> TableValueSyncReport:
        """同步单个逻辑表并返回字段级报告。

        Args:
            target: 逻辑表同步配置。
            continue_on_error: 单个字段失败后是否继续同步后续字段。

        Returns:
            单表同步报告。
        """
        started_at = time.perf_counter()
        if not target.enabled:
            return TableValueSyncReport(
                table_name=target.table_name,
                status="skipped",
                duration_ms=_elapsed_ms(started_at),
                skip_reason="target_disabled",
            )

        field_reports: list[FieldValueSyncReport] = []
        for field in target.fields:
            if not field.enabled:
                field_reports.append(
                    FieldValueSyncReport(
                        table_name=target.table_name,
                        column_name=field.column_name,
                        source_column=field.resolved_source_column,
                        status="skipped",
                        skip_reason="field_disabled",
                    )
                )
                continue
            field_started_at = time.perf_counter()
            try:
                count = self.sync_field(target, field)
                field_reports.append(
                    FieldValueSyncReport(
                        table_name=target.table_name,
                        column_name=field.column_name,
                        source_column=field.resolved_source_column,
                        status="success",
                        indexed_count=count,
                        duration_ms=_elapsed_ms(field_started_at),
                    )
                )
            except Exception as exc:
                if not continue_on_error:
                    raise
                field_reports.append(
                    FieldValueSyncReport(
                        table_name=target.table_name,
                        column_name=field.column_name,
                        source_column=field.resolved_source_column,
                        status="error",
                        duration_ms=_elapsed_ms(field_started_at),
                        error=f"{target.table_name}.{field.column_name}: {exc}",
                    )
                )

        total = sum(field.indexed_count for field in field_reports)
        if any(field.status == "error" for field in field_reports):
            status = "partial_error" if total else "error"
        elif field_reports and all(field.status == "skipped" for field in field_reports):
            status = "skipped"
        else:
            status = "success"
        return TableValueSyncReport(
            table_name=target.table_name,
            status=status,
            indexed_count=total,
            duration_ms=_elapsed_ms(started_at),
            fields=field_reports,
        )

    def sync_field(self, target: TableValueIndexTarget, field: FieldValueIndexTarget) -> int:
        """同步一个字段的 distinct 字段值。

        Args:
            target: 逻辑表同步配置。
            field: 字段同步配置。

        Returns:
            本字段写入的索引记录数量。
        """
        # 从源库流式读取 distinct 值，再构造成索引记录，避免一次性把大字段集放入内存。
        records = self._build_records(target, field, self.source_reader.iter_distinct_values(target, field))
        total = 0
        batch: list[FieldValueRecord] = []
        for record in records:
            batch.append(record)
            if len(batch) >= self.config.field_value_sync.batch_size:
                # 按 batch_size 分批 upsert，平衡数据库事务压力和写入吞吐。
                total += self.index_store.upsert_values(batch)
                batch = []
        if batch:
            total += self.index_store.upsert_values(batch)
        log.info("Synced %s rows for %s.%s", total, target.table_name, field.column_name)
        return total

    def delete_target(self, table_name: str, column_name: str | None = None) -> int:
        """删除指定逻辑表或字段的索引数据。

        Args:
            table_name: 逻辑表名。
            column_name: 可选字段名；为空时删除整张逻辑表的索引数据。

        Returns:
            被删除的索引记录数量。
        """
        return self.index_store.delete_target(table_name=table_name, column_name=column_name)

    def _build_records(
        self,
        target: TableValueIndexTarget,
        field: FieldValueIndexTarget,
        raw_values: Iterable[str],
    ) -> Iterable[FieldValueRecord]:
        """把源表原始字段值转换成可写入索引表的记录。

        Args:
            target: 逻辑表同步配置。
            field: 字段同步配置。
            raw_values: 从源表读取到的原始字段值迭代器。

        Returns:
            字段值索引记录迭代器。
        """
        for raw_value in raw_values:
            clean_value = str(raw_value).strip()
            if not clean_value:
                continue
            # alias_provider 是扩展点，主配置不维护具体 raw_value 的别名。
            aliases = self.alias_provider.aliases_for(target, field, clean_value)
            normalized_value = self.normalizer.normalize(clean_value)
            yield FieldValueRecord(
                table_name=target.table_name,
                table_comment=target.table_comment,
                column_name=field.column_name,
                column_comment=field.column_comment,
                raw_value=clean_value,
                aliases=aliases,
                normalized_value=normalized_value,
                source_schema=target.source_schema,
                source_table=target.source_table,
                source_column=field.resolved_source_column,
                metadata={
                    "filters": [filter_item.__dict__ for filter_item in target.filters],
                    "unsafe_where_clause": target.unsafe_where_clause,
                },
            )


def _elapsed_ms(started_at: float) -> float:
    """计算从 started_at 到当前的毫秒耗时。"""
    return (time.perf_counter() - started_at) * 1000.0
