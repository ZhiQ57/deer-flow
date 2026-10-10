from __future__ import annotations


async def rerank_hits(reranker, query: str, hits, top_k: int):
    if not hits or reranker is None:
        return hits[:top_k]
    ranking = await reranker.rerank(query, [hit.text for hit in hits], top_k)
    return [hits[index].model_copy(update={"score": score}) for index, score in ranking]
