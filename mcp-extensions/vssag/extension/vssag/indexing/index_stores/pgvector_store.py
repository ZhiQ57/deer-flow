from __future__ import annotations

import json
import re
from typing import Sequence

from .memory_store import MemoryIndexStore
from ...vssag_schemas.index.payload import IndexPayload
from ...vssag_schemas.graph.reference import ReferenceRecord


class PgVectorIndexStore(MemoryIndexStore):
    """PostgreSQL/pgvector adapter.

    The in-memory parent remains available for offline tests and is used when
    asyncpg or a live database is not configured.
    """

    def __init__(
        self,
        dsn: str = "",
        dimension: int = 384,
        table_name: str = "vssag_index_records",
        *,
        embedding_enabled: bool = True,
    ):
        super().__init__()
        self.dsn = _normalize_dsn(dsn)
        self.dimension = dimension
        self.embedding_enabled = embedding_enabled
        self.table_name = _safe_identifier(table_name)
        self.reference_table = f"{self.table_name}_references"
        self._connection = None
        self._initialized = False

    async def _ensure_connection(self) -> bool:
        if self._connection is not None:
            return True
        if not self.dsn:
            return False
        try:
            import asyncpg

            self._connection = await asyncpg.connect(self.dsn)
            if self.embedding_enabled:
                await self._connection.execute(
                    "CREATE EXTENSION IF NOT EXISTS vector"
                )
            embedding_column = (
                f", embedding vector({self.dimension})"
                if self.embedding_enabled
                else ""
            )
            await self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    record_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    payload JSONB NOT NULL
                    {embedding_column}
                )
                """
            )
            await self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.reference_table} (
                    edge_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_record_id TEXT,
                    target_key TEXT NOT NULL,
                    target_source_id TEXT,
                    target_record_id TEXT,
                    payload JSONB NOT NULL
                )
                """
            )
            await self._connection.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.reference_table}_source "
                f"ON {self.reference_table}(source_id)"
            )
            self._initialized = True
            return True
        except Exception:
            self._connection = None
            return False

    async def upsert(self, payload: IndexPayload) -> None:
        await super().upsert(payload)
        if not await self._ensure_connection():
            return
        try:
            if self.embedding_enabled:
                await self._connection.execute(
                    f"""
                    INSERT INTO {self.table_name}(
                        record_id, source_id, payload, embedding
                    )
                    VALUES($1, $2, $3::jsonb, $4)
                    ON CONFLICT(record_id) DO UPDATE SET
                        source_id = EXCLUDED.source_id,
                        payload = EXCLUDED.payload,
                        embedding = EXCLUDED.embedding
                    """,
                    payload.record_id,
                    payload.source_id,
                    json.dumps(payload.model_dump(mode="json")),
                    _vector_literal(payload.embedding),
                )
            else:
                await self._connection.execute(
                    f"""
                    INSERT INTO {self.table_name}(record_id, source_id, payload)
                    VALUES($1, $2, $3::jsonb)
                    ON CONFLICT(record_id) DO UPDATE SET
                        source_id = EXCLUDED.source_id,
                        payload = EXCLUDED.payload
                    """,
                    payload.record_id,
                    payload.source_id,
                    json.dumps(payload.model_dump(mode="json")),
                )
        except Exception:
            return

    async def delete(self, record_id: str) -> None:
        await super().delete(record_id)
        if self._connection is not None:
            try:
                await self._connection.execute(
                    f"DELETE FROM {self.table_name} WHERE record_id=$1",
                    record_id,
                )
                await self._connection.execute(
                    f"DELETE FROM {self.reference_table} WHERE source_record_id=$1",
                    record_id,
                )
            except Exception:
                pass

    async def add_references(self, references: Sequence[ReferenceRecord]) -> None:
        await super().add_references(references)
        if not await self._ensure_connection():
            return
        try:
            async with self._connection.transaction():
                for reference in references:
                    await self._connection.execute(
                        f"""
                        INSERT INTO {self.reference_table}(
                            edge_id, source_id, source_record_id, target_key,
                            target_source_id, target_record_id, payload
                        )
                        VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)
                        ON CONFLICT(edge_id) DO UPDATE SET
                            target_source_id=EXCLUDED.target_source_id,
                            target_record_id=EXCLUDED.target_record_id,
                            payload=EXCLUDED.payload
                        """,
                        reference.edge_id,
                        reference.source_id,
                        reference.source_record_id,
                        reference.target_key,
                        reference.target_source_id,
                        reference.target_record_id,
                        json.dumps(reference.model_dump(mode="json"), ensure_ascii=False),
                    )
        except Exception:
            return

    async def list_references(self, source_ids: Sequence[str] | None = None):
        if not await self._ensure_connection():
            return await super().list_references(source_ids)
        try:
            if source_ids:
                placeholders = ",".join(f"${index}" for index in range(1, len(source_ids) + 1))
                rows = await self._connection.fetch(
                    f"SELECT payload FROM {self.reference_table} WHERE source_id IN ({placeholders})",
                    *source_ids,
                )
            else:
                rows = await self._connection.fetch(
                    f"SELECT payload FROM {self.reference_table}"
                )
            persisted = [
                ReferenceRecord.model_validate(json.loads(row["payload"]))
                for row in rows
            ]
            self.references = {reference.edge_id: reference for reference in persisted}
        except Exception:
            pass
        return await super().list_references(source_ids)

    async def graph_neighbors(
        self,
        source_ids: Sequence[str],
        limit: int = 20,
        allowed_source_ids: Sequence[str] | None = None,
    ):
        references = await self.list_references()
        allowed = set(source_ids)
        visible = set(allowed_source_ids) if allowed_source_ids is not None else None
        outgoing: dict[str, set[str]] = {}
        for reference in references:
            outgoing.setdefault(reference.source_id, set()).add(
                reference.target_source_id or reference.target_key
            )
            if reference.target_source_id:
                outgoing.setdefault(reference.target_source_id, set()).add(reference.source_id)
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
            seeds = [source for source in allowed if neighbor in outgoing.get(source, set())]
            for record in by_source.get(neighbor, []):
                result.append((record, 1.0, seeds))
        return result[:limit]

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def list_records(self, source_ids: Sequence[str] | None = None):
        if source_ids == []:
            return []
        if not await self._ensure_connection():
            return await super().list_records(source_ids)
        try:
            if source_ids:
                placeholders = ",".join(f"${index}" for index in range(1, len(source_ids) + 1))
                rows = await self._connection.fetch(
                    f"SELECT payload FROM {self.table_name} WHERE source_id IN ({placeholders})",
                    *source_ids,
                )
            else:
                rows = await self._connection.fetch(
                    f"SELECT payload FROM {self.table_name}"
                )
            loaded: dict[str, IndexPayload] = {}
            for row in rows:
                payload = IndexPayload.model_validate(json.loads(row["payload"]))
                loaded[payload.record_id] = payload
            if source_ids is None:
                self.records = loaded
            else:
                for record_id, payload in loaded.items():
                    self.records[record_id] = payload
        except Exception:
            pass
        return await super().list_records(source_ids)

    async def search_text(
        self,
        query: str,
        source_ids: Sequence[str] | None = None,
        limit: int = 20,
    ):
        if source_ids == []:
            return []
        if not await self._ensure_connection():
            return await super().search_text(query, source_ids, limit)
        terms = [term for term in re.findall(r"[\w\u4e00-\u9fff]+", query or "") if term]
        if not terms:
            return []
        try:
            clauses = []
            args: list[str] = []
            for index, term in enumerate(terms, start=1):
                clauses.append(
                    f"(payload->>'text' ILIKE ${index} OR payload->>'normalized_text' ILIKE ${index})"
                )
                args.append(f"%{term}%")
            next_index = len(args) + 1
            scope_clause = ""
            if source_ids:
                placeholders = ",".join(
                    f"${next_index + offset}" for offset in range(len(source_ids))
                )
                scope_clause = f" AND source_id IN ({placeholders})"
                args.extend(source_ids)
            rows = await self._connection.fetch(
                f"""
                SELECT payload, (
                    {" + ".join(f"(CASE WHEN payload->>'text' ILIKE ${index} THEN 1 ELSE 0 END)" for index in range(1, len(terms) + 1))}
                ) AS score
                FROM {self.table_name}
                WHERE ({' OR '.join(clauses)}){scope_clause}
                ORDER BY score DESC
                LIMIT {max(1, int(limit))}
                """,
                *args,
            )
            return [
                (
                    IndexPayload.model_validate(json.loads(row["payload"])),
                    float(row["score"] or 0),
                )
                for row in rows
            ]
        except Exception:
            return await super().search_text(query, source_ids, limit)

    async def search_vector(
        self,
        vector: Sequence[float],
        source_ids: Sequence[str] | None = None,
        limit: int = 20,
    ):
        if source_ids == [] or not self.embedding_enabled:
            return []
        if not await self._ensure_connection():
            return await super().search_vector(vector, source_ids, limit)
        try:
            literal = _vector_literal(vector)
            scope_clause = ""
            args: list[object] = [literal]
            if source_ids:
                placeholders = ",".join(
                    f"${index}" for index in range(2, len(source_ids) + 2)
                )
                scope_clause = f" WHERE source_id IN ({placeholders})"
                args.extend(source_ids)
            else:
                scope_clause = " WHERE embedding IS NOT NULL"
            rows = await self._connection.fetch(
                f"""
                SELECT payload, 1 - (embedding <=> $1::vector) AS score
                FROM {self.table_name}
                {scope_clause}
                ORDER BY embedding <=> $1::vector
                LIMIT {max(1, int(limit))}
                """,
                *args,
            )
            return [
                (
                    IndexPayload.model_validate(json.loads(row["payload"])),
                    float(row["score"] or 0),
                )
                for row in rows
            ]
        except Exception:
            return await super().search_vector(vector, source_ids, limit)


def _normalize_dsn(dsn: str) -> str:
    """asyncpg expects a libpq URL, not SQLAlchemy's async dialect prefix."""
    value = (dsn or "").strip()
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", value)


def _safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise ValueError(f"invalid PostgreSQL table identifier: {value!r}")
    return value


def _vector_literal(values: Sequence[float] | None) -> str | None:
    if values is None:
        return None
    return "[" + ",".join(f"{float(value):.10g}" for value in values) + "]"
