from __future__ import annotations


async def search_full_text(store, query: str, source_ids: list[str] | None, top_k: int):
    return await store.search_text(query, source_ids=source_ids, limit=top_k)
