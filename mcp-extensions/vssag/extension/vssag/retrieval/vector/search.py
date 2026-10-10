from __future__ import annotations


async def search_vector(store, vector: list[float], source_ids: list[str] | None, top_k: int):
    return await store.search_vector(vector, source_ids=source_ids, limit=top_k)
