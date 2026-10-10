"""DOCX loader for local fallback parsing."""

from __future__ import annotations

import io
from pathlib import Path


def load(source: str | Path | bytes) -> str:
    from docx import Document

    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    document = Document(io.BytesIO(raw))
    return "\n\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
