from __future__ import annotations

import re
import uuid

from ...vssag_schemas.graph.reference import ReferenceRecord


_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]")
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def extract_references(
    text: str,
    *,
    source_id: str,
    source_record_id: str | None = None,
    position: int = 0,
) -> list[ReferenceRecord]:
    result: list[ReferenceRecord] = []
    for match in _WIKILINK.finditer(text or ""):
        target = match.group(1).strip()
        anchor = (match.group(2) or target).strip()
        result.append(
            ReferenceRecord(
                edge_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{position}:{target}")),
                source_id=source_id,
                source_record_id=source_record_id,
                target_key=target,
                raw_target=target,
                anchor_text=anchor,
                source_position=position,
                extraction_method="explicit_markdown",
                status="unresolved",
            )
        )
    for match in _MARKDOWN_LINK.finditer(text or ""):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        result.append(
            ReferenceRecord(
                edge_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{position}:{target}")),
                source_id=source_id,
                source_record_id=source_record_id,
                target_key=target,
                raw_target=target,
                anchor_text=target,
                source_position=position,
                extraction_method="explicit_markdown",
                status="unresolved",
            )
        )
    return result
