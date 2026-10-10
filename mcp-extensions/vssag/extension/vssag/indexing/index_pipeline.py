"""Generic index construction pipeline."""

from __future__ import annotations

from collections.abc import Iterable
from collections import OrderedDict

from ..errors import ConfigurationError
from ..providers.embedder_prov import Embedder
from ..vssag_schemas.graph.reference import ReferenceRecord
from ..vssag_schemas.index.content import ContentRecord
from ..vssag_schemas.index.payload import IndexPayload
from .index_enrichment.entities import extract_entities
from .index_enrichment.keywords import extract_keywords
from .index_enrichment.questions import generate_questions
from .index_enrichment.references import extract_references
from .index_enrichment.summaries import summarize


class IndexPipeline:
    def __init__(
        self,
        store,
        embedder: Embedder | None = None,
        embedding_version: str = "1",
        *,
        embedding_enabled: bool = True,
        graph_enabled: bool = True,
        graph_max_entities: int = 15,
    ):
        self.store = store
        self.embedder = embedder
        self.embedding_enabled = embedding_enabled
        if self.embedding_enabled and self.embedder is None:
            raise ConfigurationError(
                "Indexing with embeddings requires an injected Embedder"
            )
        self.embedding_version = embedding_version
        self.graph_enabled = graph_enabled
        self.graph_max_entities = graph_max_entities

    async def build_payload(self, record: ContentRecord) -> tuple[IndexPayload, list[ReferenceRecord]]:
        text = record.text or ""
        keywords = extract_keywords(text)
        questions = generate_questions(text, record.title)
        references: list[ReferenceRecord] = []
        if self.graph_enabled:
            references = extract_references(
                text,
                source_id=record.source_id,
                source_record_id=record.record_id,
                position=record.position,
            )
            for raw_reference in (
                record.metadata.get("references", [])
                if isinstance(record.metadata, dict)
                else []
            ):
                if isinstance(raw_reference, str):
                    target = raw_reference
                    details = {}
                elif isinstance(raw_reference, dict):
                    target = str(
                        raw_reference.get("target")
                        or raw_reference.get("target_key")
                        or ""
                    )
                    details = raw_reference
                else:
                    continue
                if not target:
                    continue
                references.append(
                    ReferenceRecord(
                        edge_id=details.get(
                            "edge_id",
                            f"{record.source_id}:{record.record_id}:{target}",
                        ),
                        source_id=record.source_id,
                        source_record_id=record.record_id,
                        target_key=target,
                        target_source_id=details.get("target_source_id"),
                        target_record_id=details.get("target_record_id"),
                        raw_target=target,
                        anchor_text=str(details.get("anchor_text") or target),
                        relation_type=str(
                            details.get("relation_type") or "references"
                        ),
                        extraction_method=str(
                            details.get("extraction_method") or "metadata"
                        ),
                        confidence=float(details.get("confidence", 1.0)),
                        status=str(details.get("status") or "unresolved"),
                        source_position=record.position,
                    )
                )
            references = list(
                OrderedDict(
                    (reference.edge_id, reference)
                    for reference in references
                ).values()
            )
        embeddings = (
            await self.embedder.embed([text])
            if self.embedding_enabled
            else []
        )
        payload = IndexPayload(
            **record.model_dump(),
            normalized_text=" ".join(text.split()),
            keywords=keywords,
            questions=questions,
            summary=summarize(text),
            embedding=embeddings[0] if embeddings else None,
            embedding_model=(
                self.embedder.model_name if self.embedding_enabled else ""
            ),
            embedding_dimension=(
                self.embedder.dimension if self.embedding_enabled else 0
            ),
            embedding_version=self.embedding_version,
            reference_ids=[ref.edge_id for ref in references],
            entity_ids=(
                [
                    item["name"]
                    for item in extract_entities(
                        text,
                        limit=self.graph_max_entities,
                    )
                ]
                if self.graph_enabled
                else []
            ),
        )
        return payload, references

    async def index_record(self, record: ContentRecord) -> IndexPayload:
        payload, references = await self.build_payload(record)
        await self.store.upsert(payload)
        if references:
            await self.store.add_references(references)
        return payload

    async def index_records(self, records: Iterable[ContentRecord]) -> list[IndexPayload]:
        result = []
        for record in records:
            result.append(await self.index_record(record))
        return result

    async def delete_record(self, record_id: str) -> None:
        await self.store.delete(record_id)

    async def rebuild_source(self, source_id: str, records: Iterable[ContentRecord]) -> list[IndexPayload]:
        existing = await self.store.list_records([source_id])
        for record in existing:
            await self.store.delete(record.record_id)
        return await self.index_records(records)
