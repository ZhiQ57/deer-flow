from __future__ import annotations


def build_citations(hits) -> list[dict]:
    citations = []
    for index, hit in enumerate(hits, start=1):
        citations.append(
            {
                "n": index,
                "record_id": hit.record_id,
                "source_id": hit.source_id,
                "title": hit.source_title,
                "heading_path": hit.heading_path,
                "snippet": hit.text[:500],
                "score": hit.score,
                "related_to": hit.related_to,
                "outgoing_links": hit.outgoing_links,
                "backlinks": hit.backlinks,
                "provenance": hit.provenance,
            }
        )
    return citations
