"""Keyword/vector/graph retrieval and result normalization."""

from __future__ import annotations

from ..errors import ConfigurationError
from ..providers.embedder_prov import Embedder
from ..vssag_schemas.retriever.hit import SearchHit
from ..vssag_schemas.retriever.request import SearchRequest
from ..vssag_schemas.retriever.response import SearchResponse
from .citations import build_citations
from .fusion import rrf_fuse
from .full_text.search import search_full_text
from .graph.search import search_graph
from .rerank import rerank_hits
from .vector.search import search_vector


class HybridRetriever:
    def __init__(
        self,
        store,
        embedder: Embedder | None = None,
        reranker=None,
        *,
        text_enabled: bool = True,
        vector_enabled: bool = True,
        graph_enabled: bool = True,
        text_weight: float = 0.4,
        vector_weight: float = 0.2,
        graph_weight: float = 0.3,
    ):
        self.store = store
        self.embedder = embedder
        self.vector_enabled = vector_enabled
        if self.vector_enabled and self.embedder is None:
            raise ConfigurationError(
                "Vector retrieval requires an injected Embedder"
            )
        self.reranker = reranker
        self.text_enabled = text_enabled
        self.graph_enabled = graph_enabled
        self.text_weight = text_weight
        self.vector_weight = vector_weight
        self.graph_weight = graph_weight

    async def search(self, request: SearchRequest) -> SearchResponse:
        source_ids = await self._resolve_scope_source_ids(request)
        lexical: list = []
        vector: list = []
        graph: list = []
        if self.text_enabled and request.strategy in {"keyword", "hybrid"}:
            lexical = await search_full_text(
                self.store, request.query, source_ids, request.top_k * 4
            )
        if self.vector_enabled and request.strategy in {"vector", "hybrid"}:
            query_vector = (await self.embedder.embed([request.query]))[0]
            vector = await search_vector(
                self.store, query_vector, source_ids, request.top_k * 4
            )
        if (
            self.graph_enabled
            and request.strategy in {"graph", "hybrid"}
            and request.include_graph
        ):
            seeds = list(
                dict.fromkeys(
                    record.source_id
                    for record, _score in [
                        *lexical[: request.top_k],
                        *vector[: request.top_k],
                    ]
                )
            )
            graph = (
                await search_graph(
                    self.store,
                    seeds,
                    request.top_k * 2,
                    allowed_source_ids=source_ids,
                )
                if seeds
                else []
            )

        if request.strategy == "keyword":
            fused = [(record, score, []) for record, score in lexical[: request.top_k]]
        elif request.strategy == "vector":
            fused = [(record, score, []) for record, score in vector[: request.top_k]]
        elif request.strategy == "graph":
            fused = graph[: request.top_k]
        else:
            fused = rrf_fuse(
                lexical,
                vector,
                graph,
                top_k=request.top_k,
                lexical_weight=self.text_weight,
                vector_weight=self.vector_weight,
                graph_weight=self.graph_weight,
            )

        graph_context = await self._graph_context(source_ids)
        hits = [
            self._build_hit(
                record=record,
                score=round(float(score), 8),
                channels=_channels(record, lexical, vector, graph),
                related=related,
                include_content=request.include_content,
                graph_context=graph_context,
            )
            for record, score, related in fused
        ]
        if self.reranker is not None:
            hits = await rerank_hits(
                self.reranker, request.query, hits, request.top_k
            )
        return SearchResponse(
            query=request.query,
            hits=hits,
            total=len(hits),
            mode=request.strategy,
            citations=build_citations(hits),
            graph_nodes=graph_context["nodes"],
            graph_edges=graph_context["edges"],
        )

    async def _resolve_scope_source_ids(
        self,
        request: SearchRequest,
    ) -> list[str] | None:
        context = request.context
        if context is None:
            return request.source_ids
        records = await self.store.list_records()
        requested_source_ids = (
            set(request.source_ids)
            if request.source_ids is not None
            else None
        )
        visible: list[str] = []
        for record in records:
            if (
                requested_source_ids is not None
                and record.source_id not in requested_source_ids
            ):
                continue
            if not context.allows_record(record.source_id, record.metadata or {}):
                continue
            visible.append(record.source_id)
        return list(dict.fromkeys(visible))

    async def _graph_context(self, source_ids: list[str] | None) -> dict[str, list[dict]]:
        references = await self.store.list_references()
        if source_ids is not None:
            allowed = set(source_ids)
            references = [
                reference
                for reference in references
                if reference.source_id in allowed
                and (
                    reference.target_source_id is None
                    or reference.target_source_id in allowed
                )
            ]
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for reference in references:
            source = reference.source_id
            target = reference.target_source_id or reference.target_key
            nodes.setdefault(
                source,
                {"node_id": source, "label": source, "node_type": "document"},
            )
            nodes.setdefault(
                target,
                {"node_id": target, "label": target, "node_type": "document"},
            )
            edges.append(
                {
                    "edge_id": reference.edge_id,
                    "source_id": source,
                    "target_id": target,
                    "relation_type": reference.relation_type,
                    "confidence": reference.confidence,
                    "evidence_record_id": reference.source_record_id,
                    "status": reference.status,
                }
            )
        return {"nodes": list(nodes.values()), "edges": edges}

    @staticmethod
    def _build_hit(
        *,
        record,
        score: float,
        channels: list[str],
        related: list[str],
        include_content: bool,
        graph_context: dict[str, list[dict]],
    ) -> SearchHit:
        outgoing = []
        backlinks = []
        for edge in graph_context["edges"]:
            if edge["source_id"] == record.source_id:
                outgoing.append(edge["target_id"])
            if edge["target_id"] == record.source_id:
                backlinks.append(edge["source_id"])
        return SearchHit(
            record_id=record.record_id,
            source_id=record.source_id,
            score=score,
            text=record.text if include_content else "",
            heading_path=record.heading_path or "",
            source_title=record.title or record.source_id,
            channels=channels,
            related_to=related,
            outgoing_links=sorted(set(outgoing)),
            backlinks=sorted(set(backlinks)),
            provenance={
                "source_id": record.source_id,
                "record_id": record.record_id,
                "position": record.position,
                "index_version": getattr(record, "index_version", "1"),
            },
            metadata=record.metadata,
        )


def _channels(record, lexical, vector, graph) -> list[str]:
    channels = []
    if any(item.record_id == record.record_id for item, _ in lexical):
        channels.append("full_text")
    if any(item.record_id == record.record_id for item, _ in vector):
        channels.append("vector")
    if any(item.record_id == record.record_id for item, _, _ in graph):
        channels.append("graph")
    return channels
