"""LanceDB content store with a stable, JSON-backed row schema.

LanceDB/Arrow cannot reliably infer nested Pydantic dictionaries and variable
length lists from arbitrary metadata. The adapter therefore persists the full
payload as JSON and keeps only scalar identifiers plus the embedding column in
the Lance table. Retrieval still uses the common in-memory search algorithms,
after loading rows through this stable schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from ...vssag_schemas.graph.reference import ReferenceRecord
from ...vssag_schemas.index.payload import IndexPayload
from .memory_store import MemoryIndexStore
from .sqlite_graph_store import SQLiteGraphStore


class LanceDBIndexStore(MemoryIndexStore):
    """LanceDB adapter with deterministic fallback when the extra is absent."""

    def __init__(
        self,
        uri: str = "./data/vssag.lancedb",
        table_name: str = "content_records",
        graph_path: str | None = None,
    ):
        super().__init__()
        self.uri = uri
        self.table_name = table_name
        self._db = None
        self._table = None
        self._pa = None
        self.graph_store = SQLiteGraphStore(
            graph_path or str(Path(uri).with_suffix(".graph.sqlite3"))
        )
        try:
            import lancedb
            import pyarrow as pa

            Path(uri).parent.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(uri)
            self._pa = pa
        except Exception:
            self._db = None
            self._pa = None

    def _ensure_table(self) -> None:
        if self._db is None or self._table is not None:
            return
        try:
            self._table = self._db.open_table(self.table_name)
        except Exception:
            self._table = None

    def _table_for_payload(self, payload: IndexPayload):
        values = [float(value) for value in (payload.embedding or [])]
        if not values:
            raise ValueError("LanceDB records require a non-empty embedding")
        embedding = self._pa.FixedSizeListArray.from_arrays(
            self._pa.array(values, type=self._pa.float32()),
            len(values),
        )
        return self._pa.Table.from_arrays(
            [
                self._pa.array([payload.record_id]),
                self._pa.array([payload.source_id]),
                self._pa.array(
                    [
                        json.dumps(
                            payload.model_dump(mode="json"),
                            ensure_ascii=False,
                        )
                    ]
                ),
                embedding,
            ],
            names=["record_id", "source_id", "payload_json", "embedding"],
        )

    async def upsert(self, payload: IndexPayload) -> None:
        await super().upsert(payload)
        await self.graph_store.resolve_targets(self._resolve_target)
        if self._db is None or self._pa is None:
            return
        try:
            self._ensure_table()
            table = self._table_for_payload(payload)
            if self._table is None:
                self._table = self._db.create_table(self.table_name, data=table)
                return
            self._table.delete(f"record_id = '{_escape_literal(payload.record_id)}'")
            self._table.add(table)
        except Exception:
            # Keep the in-memory copy usable if an optional backend is faulty.
            return

    async def delete(self, record_id: str) -> None:
        await super().delete(record_id)
        await self.graph_store.delete_source_record(record_id)
        if self._table is not None:
            try:
                self._table.delete(f"record_id = '{_escape_literal(record_id)}'")
            except Exception:
                pass

    async def list_records(
        self,
        source_ids: Sequence[str] | None = None,
    ) -> list[IndexPayload]:
        if source_ids == []:
            return []
        self._ensure_table()
        if self._table is not None:
            try:
                rows = self._table.to_arrow().to_pylist()
                loaded = {}
                for row in rows:
                    payload_json = row.get("payload_json")
                    if not payload_json:
                        continue
                    payload = IndexPayload.model_validate(json.loads(payload_json))
                    loaded[payload.record_id] = payload
                self.records = loaded
            except Exception:
                # Old/incompatible tables should not break the memory fallback.
                pass
        return await super().list_records(source_ids)

    async def add_references(
        self,
        references: Sequence[ReferenceRecord],
    ) -> None:
        for reference in references:
            target = self._resolve_target(reference.target_key)
            if target is not None:
                reference.target_source_id = target.source_id
                reference.target_record_id = target.record_id
                reference.status = "resolved"
        await super().add_references(references)
        await self.graph_store.add_references(references)

    async def list_references(
        self,
        source_ids: Sequence[str] | None = None,
    ) -> list[ReferenceRecord]:
        persisted = await self.graph_store.list_references(source_ids)
        if persisted:
            self.references = {item.edge_id: item for item in persisted}
        return await super().list_references(source_ids)

    async def graph_neighbors(
        self,
        source_ids: Sequence[str],
        limit: int = 20,
        allowed_source_ids: Sequence[str] | None = None,
    ):
        references = await self.list_references()
        allowed = set(source_ids)
        visible = (
            set(allowed_source_ids)
            if allowed_source_ids is not None
            else None
        )
        outgoing: dict[str, set[str]] = {}
        for reference in references:
            outgoing.setdefault(reference.source_id, set()).add(
                reference.target_source_id or reference.target_key
            )
            if reference.target_source_id:
                outgoing.setdefault(reference.target_source_id, set()).add(
                    reference.source_id
                )
        neighbor_ids = {
            neighbor
            for source_id in allowed
            for neighbor in outgoing.get(source_id, set())
            if neighbor not in allowed
            and (visible is None or neighbor in visible)
        }
        records = await self.list_records()
        by_source: dict[str, list[IndexPayload]] = {}
        for record in records:
            by_source.setdefault(record.source_id, []).append(record)
        result = []
        for neighbor in neighbor_ids:
            seeds = [
                source
                for source in allowed
                if neighbor in outgoing.get(source, set())
            ]
            for record in by_source.get(neighbor, []):
                result.append((record, 1.0, seeds))
        return result[:limit]

    async def close(self) -> None:
        self._table = None
        self._db = None

    def _resolve_target(self, target_key: str) -> IndexPayload | None:
        normalized = (target_key or "").strip().lower().removesuffix(".md")
        if not normalized:
            return None
        for record in self.records.values():
            candidates = {
                record.source_id.lower(),
                (record.title or "").strip().lower(),
                str(record.metadata.get("slug", "")).strip().lower(),
                str(record.metadata.get("path", ""))
                .strip()
                .lower()
                .removesuffix(".md"),
            }
            if normalized in candidates:
                return record
        return None


def _escape_literal(value: str) -> str:
    return str(value).replace("'", "''")
