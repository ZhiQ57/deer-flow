from __future__ import annotations


def summarize(text: str, max_chars: int = 320) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "…"
