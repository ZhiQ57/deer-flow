"""In-memory store used by default and as a safe fallback for optional backends."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Sequence

from ...vssag_schemas.graph.reference import ReferenceRecord
from ...vssag_schemas.index.payload import IndexPayload


class MemoryIndexStore:
    def __init__(self):
        self.records: dict[str, IndexPayload] = {}
        self.references: dict[str, ReferenceRecord] = {}

    async def upsert(self, payload: IndexPayload) -> None:
        self.records[payload.record_id] = payload
        self._resolve_pending_references()

    async def delete(self, record_id: str) -> None:
        deleted = self.records.pop(record_id, None)
        if deleted is None:
            return
        for key in [
            key
            for key, ref in self.references.items()
            if ref.source_record_id == record_id
        ]:
            self.references.pop(key, None)
        # Edges targeting the deleted source become unresolved rather than
        # pointing at stale records.
        for reference in self.references.values():
            if reference.target_record_id == record_id or reference.target_source_id == deleted.source_id:
                reference.target_record_id = None
                reference.target_source_id = None
                reference.status = "unresolved"

    async def get(self, record_id: str) -> IndexPayload | None:
        return self.records.get(record_id)

    async def list_records(self, source_ids: Sequence[str] | None = None) -> list[IndexPayload]:
        allowed = set(source_ids) if source_ids is not None else None
        return [
            record for record in self.records.values()
            if allowed is None or record.source_id in allowed
        ]

    async def add_references(self, references: Sequence[ReferenceRecord]) -> None:
        for reference in references:
            target = self._resolve_target(reference.target_key)
            if target is not None:
                reference.target_source_id = target.source_id
                reference.target_record_id = target.record_id
                reference.status = "resolved"
            self.references[reference.edge_id] = reference

    async def list_references(self, source_ids: Sequence[str] | None = None) -> list[ReferenceRecord]:
        allowed = set(source_ids) if source_ids is not None else None
        return [
            ref for ref in self.references.values()
            if allowed is None or ref.source_id in allowed
        ]

    async def search_text(self, query: str, source_ids: Sequence[str] | None = None, limit: int = 20) -> list[tuple[IndexPayload, float]]:
        terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query or "")]
        scored: list[tuple[IndexPayload, float]] = []
        for record in await self.list_records(source_ids):
            haystack = " ".join(
                [record.text, record.title or "", record.heading_path or "", *record.keywords, *record.questions]
            ).lower()
            if not haystack:
                continue
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((record, float(score)))
        scored.sort(key=lambda item: (-item[1], item[0].position, item[0].record_id))
        return scored[:limit]

    async def search_vector(self, vector: Sequence[float], source_ids: Sequence[str] | None = None, limit: int = 20) -> list[tuple[IndexPayload, float]]:
        def cosine(left: Sequence[float], right: Sequence[float]) -> float:
            numerator = sum(a * b for a, b in zip(left, right))
            den = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
            return numerator / den if den else 0.0

        scored = [
            (record, cosine(vector, record.embedding or []))
            for record in await self.list_records(source_ids)
            if record.embedding
        ]
        scored.sort(key=lambda item: (-item[1], item[0].record_id))
        return scored[:limit]

    async def graph_neighbors(
        self,
        source_ids: Sequence[str],
        limit: int = 20,
        allowed_source_ids: Sequence[str] | None = None,
    ) -> list[tuple[IndexPayload, float, list[str]]]:
        allowed = set(source_ids)
        visible = set(allowed_source_ids) if allowed_source_ids is not None else None
        refs = await self.list_references()
        outgoing: defaultdict[str, set[str]] = defaultdict(set)
        for ref in refs:
            outgoing[ref.source_id].add(ref.target_source_id or ref.target_key)
            if ref.target_source_id:
                outgoing[ref.target_source_id].add(ref.source_id)
        neighbor_ids = {
            neighbor
            for source_id in allowed
            for neighbor in outgoing.get(source_id, set())
            if neighbor not in allowed
            and (visible is None or neighbor in visible)
        }
        records = await self.list_records()
        by_source: defaultdict[str, list[IndexPayload]] = defaultdict(list)
        for record in records:
            by_source[record.source_id].append(record)
        result: list[tuple[IndexPayload, float, list[str]]] = []
        for neighbor in neighbor_ids:
            for record in by_source.get(neighbor, []):
                seeds = [source for source in allowed if neighbor in outgoing.get(source, set())]
                result.append((record, 1.0, seeds))
        return result[:limit]

    def _resolve_target(self, target_key: str) -> IndexPayload | None:
        normalized = (target_key or "").strip().lower().removesuffix(".md")
        if not normalized:
            return None
        for record in self.records.values():
            candidates = {
                record.source_id.lower(),
                (record.title or "").strip().lower(),
                str(record.metadata.get("slug", "")).strip().lower(),
                str(record.metadata.get("path", "")).strip().lower().removesuffix(".md"),
            }
            if normalized in candidates:
                return record
        return None

    def _resolve_pending_references(self) -> None:
        for reference in self.references.values():
            target = self._resolve_target(reference.target_key)
            if target is not None:
                reference.target_source_id = target.source_id
                reference.target_record_id = target.record_id
                reference.status = "resolved"

    async def close(self) -> None:
        return None
