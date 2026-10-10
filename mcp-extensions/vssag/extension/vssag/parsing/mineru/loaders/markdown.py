from __future__ import annotations

from pathlib import Path

from ..utils.markdown_normalize import normalize_markdown


def load(source: str | Path | bytes) -> str:
    if isinstance(source, bytes):
        return normalize_markdown(source.decode("utf-8", errors="replace"))
    return normalize_markdown(Path(source).read_text(encoding="utf-8"))
