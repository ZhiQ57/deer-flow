"""Schema 加权融合粗排实现。"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from ...configs import RetrievalSettings
from ...schemas import ColumnRetrievalResult, TableRetrievalResult, ValueRetrievalResult
from .base import RetrievalFusionBase
from .candidates import merge_table_hits, tables_from_columns, tables_from_values


class WeightedSchemaFusion(RetrievalFusionBase):
    """轻量级 Schema 融合器，用基础分、实体命中、字段和值反推表信号做粗排。"""

    def __init__(self, settings: RetrievalSettings | None = None):
        """初始化加权融合器。

        Args:
            settings: 在线检索配置，提供 reranker 和 hybrid 融合权重；为空时使用默认配置。

        Returns:
            None。
        """
        self.settings = settings or RetrievalSettings()

    def fuse_tables(
        self,
        query: str,
        tables: Sequence[TableRetrievalResult],
        columns: Sequence[ColumnRetrievalResult],
        values: Sequence[ValueRetrievalResult],
    ) -> list[TableRetrievalResult]:
        """融合候选表。

        Args:
            query: 用户问题。
            tables: 表召回结果。
            columns: 字段召回结果，用于给所属表补充分数。
            values: 字段值召回结果，用于给所属表补充分数。

        Returns:
            粗排后的表候选。
        """
        table_candidates = merge_table_hits(
            tables,
            tables_from_columns(columns),
            tables_from_values(values),
        )
        column_scores = defaultdict(float)
        for column in columns:
            column_scores[column.table_name] = max(column_scores[column.table_name], column.score)

        value_scores = defaultdict(float)
        for value in values:
            value_scores[value.table_name] = max(value_scores[value.table_name], value.score)

        fused: list[TableRetrievalResult] = []
        for table in table_candidates:
            entity_score = _entity_overlap_score(query, table.table_entities)
            column_boost = column_scores[table.table_name] * self.settings.hybrid.column_table_boost
            value_boost = value_scores[table.table_name] * self.settings.hybrid.value_table_boost
            score = table.score + entity_score * self.settings.reranker.weights.entity_boost + column_boost + value_boost
            source_scores = {
                **table.source_scores,
                "entity_overlap": entity_score,
                "column_backref": column_boost,
                "value_backref": value_boost,
                "fusion": score,
                "rerank": score,
            }
            fused.append(replace(table, score=score, source_scores=source_scores))
        return sorted(fused, key=lambda item: item.score, reverse=True)

    def fuse_columns(self, query: str, columns: Sequence[ColumnRetrievalResult]) -> list[ColumnRetrievalResult]:
        """融合候选字段。

        Args:
            query: 用户问题。
            columns: 字段召回结果。

        Returns:
            粗排后的字段候选。
        """
        fused: list[ColumnRetrievalResult] = []
        for column in columns:
            entity_score = _entity_overlap_score(query, column.column_entities)
            score = column.score + entity_score * self.settings.reranker.weights.entity_boost
            source_scores = {
                **column.source_scores,
                "entity_overlap": entity_score,
                "fusion": score,
                "rerank": score,
            }
            fused.append(replace(column, score=score, source_scores=source_scores))
        return sorted(fused, key=lambda item: item.score, reverse=True)

def _entity_overlap_score(query: str, entities: Sequence[str]) -> float:
    """计算查询文本与实体标签的重合分。

    Args:
        query: 用户问题。
        entities: 表或字段索引中的实体标签。

    Returns:
        0 到 1 之间的实体命中比例。
    """
    normalized_query = _normalize_text(query)
    clean_entities = [_normalize_text(entity) for entity in entities if entity]
    if not normalized_query or not clean_entities:
        return 0.0
    hits = sum(1 for entity in clean_entities if entity and entity in normalized_query)
    return hits / len(clean_entities)


def _normalize_text(value: str) -> str:
    """执行轻量文本归一化。

    Args:
        value: 原始文本。

    Returns:
        去空格、去标点、统一全半角和大小写后的文本。
    """
    text = unicodedata.normalize("NFKC", value).lower()
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


__all__ = ["WeightedSchemaFusion"]
