from __future__ import annotations

from collections import defaultdict


def rrf_fuse(
    lexical: list[tuple[object, float]],
    vector: list[tuple[object, float]],
    graph: list[tuple[object, float, list[str]]],
    *,
    top_k: int,
    lexical_weight: float = 0.4,
    vector_weight: float = 0.2,
    graph_weight: float = 0.3,
) -> list[tuple[object, float, list[str]]]:
    scores: defaultdict[str, float] = defaultdict(float)
    values: dict[str, object] = {}
    related: defaultdict[str, set[str]] = defaultdict(set)
    k = 60.0
    for rank, (record, _score) in enumerate(lexical, start=1):
        key = record.record_id
        values[key] = record
        scores[key] += lexical_weight / (k + rank)
    for rank, (record, _score) in enumerate(vector, start=1):
        key = record.record_id
        values[key] = record
        scores[key] += vector_weight / (k + rank)
    for rank, (record, _score, seeds) in enumerate(graph, start=1):
        key = record.record_id
        values[key] = record
        scores[key] += graph_weight / (k + rank)
        related[key].update(seeds)
    keys = sorted(scores, key=lambda key: (-scores[key], key))
    return [(values[key], scores[key], sorted(related[key])) for key in keys[:top_k]]
