from __future__ import annotations

from typing import Any, Protocol, Sequence

from ...vssag_schemas.index.payload import IndexPayload
from ...vssag_schemas.graph.reference import ReferenceRecord


class IndexStore(Protocol):
    async def upsert(self, payload: IndexPayload) -> None:
        ...

    async def delete(self, record_id: str) -> None:
        ...

    async def get(self, record_id: str) -> IndexPayload | None:
        ...

    async def list_records(self, source_ids: Sequence[str] | None = None) -> list[IndexPayload]:
        ...

    async def add_references(self, references: Sequence[ReferenceRecord]) -> None:
        ...

    async def list_references(self, source_ids: Sequence[str] | None = None) -> list[ReferenceRecord]:
        ...

    async def search_text(
        self,
        query: str,
        source_ids: Sequence[str] | None = None,
        limit: int = 20,
    ) -> list[tuple[IndexPayload, float]]:
        ...

    async def search_vector(
        self,
        vector: Sequence[float],
        source_ids: Sequence[str] | None = None,
        limit: int = 20,
    ) -> list[tuple[IndexPayload, float]]:
        ...

    async def graph_neighbors(
        self,
        source_ids: Sequence[str],
        limit: int = 20,
        allowed_source_ids: Sequence[str] | None = None,
    ) -> list[tuple[IndexPayload, float, list[str]]]:
        ...
