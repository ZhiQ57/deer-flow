from __future__ import annotations

import re
from typing import Any


async def restore_heading_levels(markdown: str, llm: Any = None) -> str:
    """Conservatively remove heading jumps; an LLM may be added later."""

    del llm
    lines = markdown.splitlines()
    previous = 0
    output: list[str] = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            output.append(line)
            continue
        level = len(match.group(1))
        if previous and level > previous + 1:
            level = previous + 1
        previous = level
        output.append(f"{'#' * level} {match.group(2).strip()}")
    return "\n".join(output) + ("\n" if output else "")
