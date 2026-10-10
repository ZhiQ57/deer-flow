"""Dependency-light PDF loader used by the local parser."""

from __future__ import annotations

import io
from pathlib import Path


def load(source: str | Path | bytes) -> str:
    from pypdf import PdfReader

    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    reader = PdfReader(io.BytesIO(raw))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)
