from __future__ import annotations

from typing import Any

from ....vssag_schemas.parse.content_item import ParsedContentItem
from ....vssag_schemas.parse.document import ParsedDocument
from ..chunking.sections import build_sections


def content_list_to_document(
    source_name: str,
    content_list: list[dict[str, Any]],
    *,
    markdown: str = "",
    parser: str = "mineru",
    metadata: dict[str, Any] | None = None,
) -> ParsedDocument:
    items: list[ParsedContentItem] = []
    for index, raw in enumerate(content_list):
        kind = str(raw.get("type", "text")).lower()
        text = str(raw.get("text") or raw.get("text_format") or raw.get("table_body") or "")
        level = int(raw.get("text_level") or 0)
        if kind == "text" and level > 0:
            item_type = "heading"
        elif kind not in {"text", "image", "table", "equation", "code", "list"}:
            item_type = "other"
        else:
            item_type = "paragraph" if kind == "text" else kind
        items.append(
            ParsedContentItem(
                item_id=f"{source_name}:{index}",
                type=item_type,
                text=text,
                level=level,
                page_idx=raw.get("page_idx"),
                position=index,
                metadata=dict(raw),
            )
        )
    if markdown:
        document = build_sections(source_name, markdown, parser=parser)
        document.items = items or document.items
        document.metadata.update(metadata or {})
        return document
    from ..chunking.sections import sections_from_items

    return ParsedDocument(
        source_name=source_name,
        parser=parser,
        markdown="\n\n".join(item.text for item in items),
        items=items,
        sections=sections_from_items(items),
        metadata=metadata or {},
    )
