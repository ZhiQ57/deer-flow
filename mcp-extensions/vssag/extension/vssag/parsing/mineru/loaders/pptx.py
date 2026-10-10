"""PPTX loader for local fallback parsing."""

from __future__ import annotations

import io
from pathlib import Path


def load(source: str | Path | bytes) -> str:
    from pptx import Presentation

    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    presentation = Presentation(io.BytesIO(raw))
    slides = []
    for number, slide in enumerate(presentation.slides, start=1):
        texts = [
            shape.text
            for shape in slide.shapes
            if hasattr(shape, "text") and shape.text.strip()
        ]
        slides.append(f"# Slide {number}\n\n" + "\n\n".join(texts))
    return "\n\n".join(slides)
