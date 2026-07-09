"""检索器内部可复用辅助方法。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TypeVar

from ..reranker.fusion.rrf import reciprocal_rank_fusion_scores
from ..schemas import (
    ColumnRetrievalResult,
    EvidenceRetrievalResult,
    TableRetrievalResult,
    ValueRetrievalResult,
)


ResultT = TypeVar(
    "ResultT",
    EvidenceRetrievalResult,
    TableRetrievalResult,
    ColumnRetrievalResult,
    ValueRetrievalResult,
)


KEYWORD_COVERAGE_WEIGHT = 0.05
"""关键词覆盖率加分权重，保持为小幅 boost，避免覆盖主召回分。"""


def split_retrieval_keywords(text: str) -> list[str]:
    """按空白和常见中英文分隔符拆分检索关键词。

    Args:
        text: 业务侧抽取后的关键词字符串，例如 ``"测试员 患者 昨天 账单信息"``。

    Returns:
        去重后的关键词列表。
    """
    if not isinstance(text, str):
        raise TypeError("retrieval keywords text must be a string")
    parts = re.split(r"[\s,，;；|、]+", text.strip())
    return normalize_retrieval_keywords(parts)


def normalize_retrieval_keywords(keywords: Sequence[str]) -> list[str]:
    """清理关键词列表并按原始顺序去重。

    Args:
        keywords: 关键词列表。

    Returns:
        标准化后的关键词列表。
    """
    if isinstance(keywords, str):
        raise TypeError("keywords must be a sequence of strings; use split_retrieval_keywords() for text")
    normalized: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        if not isinstance(keyword, str):
            raise TypeError("retrieval keyword must be a string")
        value = keyword.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def merge_evidence_keyword_hits(
    keyword_hits: Sequence[tuple[str, Sequence[EvidenceRetrievalResult]]],
    *,
    rrf_k: int = 60,
) -> list[EvidenceRetrievalResult]:
    """融合多个关键词召回的 Evidence 结果。

    Args:
        keyword_hits: 关键词及其召回结果列表。
        rrf_k: RRF 排名平滑常数。

    Returns:
        按融合分排序的 Evidence 结果。
    """
    return _merge_keyword_hits(
        keyword_hits,
        key_fn=lambda item: f"{item.evidence_type or ''}\n{item.evidence_content}",
        rrf_k=rrf_k,
    )


def merge_table_keyword_hits(
    keyword_hits: Sequence[tuple[str, Sequence[TableRetrievalResult]]],
    *,
    rrf_k: int = 60,
) -> list[TableRetrievalResult]:
    """融合多个关键词召回的表结果。

    Args:
        keyword_hits: 关键词及其召回结果列表。
        rrf_k: RRF 排名平滑常数。

    Returns:
        按融合分排序的表召回结果。
    """
    return _merge_keyword_hits(keyword_hits, key_fn=lambda item: item.table_name, rrf_k=rrf_k)


def merge_column_keyword_hits(
    keyword_hits: Sequence[tuple[str, Sequence[ColumnRetrievalResult]]],
    *,
    rrf_k: int = 60,
) -> list[ColumnRetrievalResult]:
    """融合多个关键词召回的列字段结果。

    Args:
        keyword_hits: 关键词及其召回结果列表。
        rrf_k: RRF 排名平滑常数。

    Returns:
        按融合分排序的列字段召回结果。
    """
    return _merge_keyword_hits(
        keyword_hits,
        key_fn=lambda item: f"{item.table_name}.{item.column_name}",
        rrf_k=rrf_k,
    )


def merge_value_keyword_hits(
    keyword_hits: Sequence[tuple[str, Sequence[ValueRetrievalResult]]],
    *,
    rrf_k: int = 60,
) -> list[ValueRetrievalResult]:
    """融合多个关键词召回的字段值结果。

    Args:
        keyword_hits: 关键词及其召回结果列表。
        rrf_k: RRF 排名平滑常数。

    Returns:
        按融合分排序的字段值召回结果。
    """
    return _merge_keyword_hits(
        keyword_hits,
        key_fn=lambda item: f"{item.table_name}.{item.column_name}.{item.raw_value}",
        rrf_k=rrf_k,
    )


def _merge_keyword_hits(
    keyword_hits: Sequence[tuple[str, Sequence[ResultT]]],
    *,
    key_fn: Callable[[ResultT], str],
    rrf_k: int,
) -> list[ResultT]:
    """按关键词维度融合召回结果。

    Args:
        keyword_hits: 关键词和对应召回结果。
        key_fn: 结果对象到去重 key 的映射。
        rrf_k: RRF 排名平滑常数。

    Returns:
        融合后的结果列表。
    """
    cleaned_keywords: list[str] = []
    groups: list[Sequence[ResultT]] = []
    for keyword, hits in keyword_hits:
        clean_keyword = str(keyword).strip()
        if not clean_keyword:
            continue
        cleaned_keywords.append(clean_keyword)
        groups.append(hits)
    if not cleaned_keywords:
        return []

    best_by_key: dict[str, ResultT] = {}
    best_keyword_by_key: dict[str, str] = {}
    keyword_scores_by_key: dict[str, dict[str, float]] = {}
    rank_groups: list[list[str]] = []
    for keyword, hits in zip(cleaned_keywords, groups, strict=True):
        rank_group: list[str] = []
        for hit in hits:
            key = key_fn(hit)
            rank_group.append(key)
            keyword_scores = keyword_scores_by_key.setdefault(key, {})
            keyword_scores[keyword] = max(keyword_scores.get(keyword, 0.0), float(hit.score or 0.0))
            current = best_by_key.get(key)
            if current is None or hit.score > current.score:
                best_by_key[key] = hit
                best_keyword_by_key[key] = keyword
        rank_groups.append(rank_group)

    rrf_scores = reciprocal_rank_fusion_scores(rank_groups, k=rrf_k)
    merged: list[ResultT] = []
    total_keywords = len(cleaned_keywords)
    for key, best_hit in best_by_key.items():
        keyword_scores = keyword_scores_by_key.get(key, {})
        matched_keywords = [keyword for keyword in cleaned_keywords if keyword in keyword_scores]
        coverage = len(matched_keywords) / total_keywords if total_keywords else 0.0
        coverage_boost = coverage * KEYWORD_COVERAGE_WEIGHT
        rrf_score = rrf_scores.get(key, 0.0)
        final_score = float(best_hit.score or 0.0) + rrf_score + coverage_boost
        metadata = {
            **best_hit.metadata,
            "keylist": {
                "matched_keywords": matched_keywords,
                "best_keyword": best_keyword_by_key.get(key),
                "keyword_scores": keyword_scores,
                "keyword_rrf": rrf_score,
                "keyword_coverage": coverage,
            },
        }
        source_scores = {
            **getattr(best_hit, "source_scores", {}),
            "keylist_best": float(best_hit.score or 0.0),
            "keylist_rrf": rrf_score,
            "keylist_coverage": coverage,
            "keylist_coverage_boost": coverage_boost,
            "keylist_final": final_score,
        }
        replace_kwargs = {"score": final_score, "metadata": metadata}
        if hasattr(best_hit, "source_scores"):
            replace_kwargs["source_scores"] = source_scores
        merged.append(replace(best_hit, **replace_kwargs))
    return sorted(merged, key=lambda item: item.score, reverse=True)


__all__ = [
    "merge_column_keyword_hits",
    "merge_evidence_keyword_hits",
    "merge_table_keyword_hits",
    "merge_value_keyword_hits",
    "normalize_retrieval_keywords",
    "split_retrieval_keywords",
]
