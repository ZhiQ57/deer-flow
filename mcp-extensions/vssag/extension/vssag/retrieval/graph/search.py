from __future__ import annotations


async def search_graph(
    store,
    seed_source_ids: list[str],
    top_k: int,
    allowed_source_ids: list[str] | None = None,
):
    return await store.graph_neighbors(
        seed_source_ids,
        limit=top_k,
        allowed_source_ids=allowed_source_ids,
    )
