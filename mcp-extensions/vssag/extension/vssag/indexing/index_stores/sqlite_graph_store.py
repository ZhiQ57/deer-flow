"""Small SQLite persistence adapter for LanceDB/local deployments."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Sequence

from ...vssag_schemas.graph.reference import ReferenceRecord


class SQLiteGraphStore:
    def __init__(self, path: str = "./data/vssag-graph.sqlite3"):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reference_edges (
                    edge_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_record_id TEXT,
                    target_key TEXT NOT NULL,
                    target_source_id TEXT,
                    target_record_id TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reference_source ON reference_edges(source_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reference_target ON reference_edges(target_source_id)"
            )

    async def add_references(self, references: Sequence[ReferenceRecord]) -> None:
        await asyncio.to_thread(self._add_sync, references)

    def _add_sync(self, references: Sequence[ReferenceRecord]) -> None:
        with sqlite3.connect(self.path) as connection:
            for reference in references:
                connection.execute(
                    """
                    INSERT INTO reference_edges(edge_id, source_id, source_record_id,
                        target_key, target_source_id, target_record_id, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(edge_id) DO UPDATE SET
                        target_source_id=excluded.target_source_id,
                        target_record_id=excluded.target_record_id,
                        payload=excluded.payload
                    """,
                    (
                        reference.edge_id,
                        reference.source_id,
                        reference.source_record_id,
                        reference.target_key,
                        reference.target_source_id,
                        reference.target_record_id,
                        json.dumps(reference.model_dump(mode="json"), ensure_ascii=False),
                    ),
                )

    async def list_references(self, source_ids: Sequence[str] | None = None) -> list[ReferenceRecord]:
        return await asyncio.to_thread(self._list_sync, source_ids)

    async def delete_source_record(self, record_id: str) -> None:
        """Remove outgoing edges for a deleted content record."""
        await asyncio.to_thread(self._delete_source_record_sync, record_id)

    def _delete_source_record_sync(self, record_id: str) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "DELETE FROM reference_edges WHERE source_record_id = ?",
                (record_id,),
            )

    async def resolve_targets(self, resolver) -> None:
        """Resolve pending targets without coupling the graph store to an index store."""
        references = await self.list_references()
        changed: list[ReferenceRecord] = []
        for reference in references:
            target = resolver(reference.target_key)
            if target is None:
                continue
            if (
                reference.target_source_id != target.source_id
                or reference.status != "resolved"
            ):
                reference.target_source_id = target.source_id
                reference.target_record_id = target.record_id
                reference.status = "resolved"
                changed.append(reference)
        if changed:
            await self.add_references(changed)

    def _list_sync(self, source_ids: Sequence[str] | None) -> list[ReferenceRecord]:
        with sqlite3.connect(self.path) as connection:
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                rows = connection.execute(
                    f"SELECT payload FROM reference_edges WHERE source_id IN ({placeholders})",
                    tuple(source_ids),
                ).fetchall()
            else:
                rows = connection.execute("SELECT payload FROM reference_edges").fetchall()
        return [ReferenceRecord.model_validate(json.loads(row[0])) for row in rows]
