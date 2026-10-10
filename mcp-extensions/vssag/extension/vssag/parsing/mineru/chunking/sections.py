from __future__ import annotations

import re
from typing import Iterable

from ....vssag_schemas.parse.content_item import ParsedContentItem
from ....vssag_schemas.parse.document import ParsedDocument
from ....vssag_schemas.parse.section import ParsedSection


def build_sections(source_name: str, markdown: str, *, parser: str) -> ParsedDocument:
    items: list[ParsedContentItem] = []
    stack: list[tuple[int, str]] = []
    for position, chunk in enumerate(re.split(r"\n{2,}", markdown)):
        text = chunk.strip()
        if not text:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", text.splitlines()[0])
        if heading and len(text.splitlines()) == 1:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            items.append(
                ParsedContentItem(
                    item_id=f"{source_name}:{position}",
                    type="heading",
                    text=title,
                    level=level,
                    position=position,
                    heading_path=" > ".join(item[1] for item in stack),
                )
            )
            continue
        heading_path = " > ".join(item[1] for item in stack)
        item_type = _special_type(text)
        items.append(
            ParsedContentItem(
                item_id=f"{source_name}:{position}",
                type=item_type,
                text=text,
                position=position,
                heading_path=heading_path,
            )
        )
    return ParsedDocument(
        source_name=source_name,
        parser=parser,
        markdown=markdown,
        items=items,
        sections=sections_from_items(items),
    )


def sections_from_items(items: Iterable[ParsedContentItem]) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    current: ParsedSection | None = None
    for item in items:
        if item.type == "heading":
            current = ParsedSection(
                title=item.text,
                level=item.level,
                position=item.position,
                heading_path=item.heading_path,
            )
            sections.append(current)
        elif current is None:
            current = ParsedSection(title="", level=1, position=item.position)
            sections.append(current)
            current.items.append(item)
        else:
            current.items.append(item)
    return sections


def _special_type(text: str) -> str:
    if re.match(r"^!\[[^\]]*\]\([^)]+\)$", text):
        return "image"
    if text.startswith("```"):
        return "code"
    if "|" in text and "\n" in text:
        return "table"
    if text.startswith("$$") and text.endswith("$$"):
        return "equation"
    return "paragraph"
