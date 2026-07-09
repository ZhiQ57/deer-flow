"""融合分数归一化工具。"""

from __future__ import annotations

from collections.abc import Sequence


def min_max_normalize_scores(scores: Sequence[float]) -> list[float]:
    """把分数按 min-max 归一化到 0 到 1。

    Args:
        scores: 原始分数序列。

    Returns:
        与输入顺序一致的归一化分数。
    """
    if not scores:
        return []
    min_score = min(scores)
    max_score = max(scores)
    if max_score == min_score:
        return [1.0 for _ in scores]
    return [(score - min_score) / (max_score - min_score) for score in scores]


def rank_normalize_scores(scores: Sequence[float]) -> list[float]:
    """按分数排名归一化，最高分为 1。

    Args:
        scores: 原始分数序列。

    Returns:
        与输入顺序一致的排名归一化分数。
    """
    if not scores:
        return []
    ordered = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
    normalized = [0.0 for _ in scores]
    total = len(scores)
    if total == 1:
        return [1.0]
    for rank, (index, _) in enumerate(ordered, start=1):
        normalized[index] = (total - rank) / (total - 1)
    return normalized


__all__ = ["min_max_normalize_scores", "rank_normalize_scores"]
