"""XLSX loader for local fallback parsing."""

from __future__ import annotations

import io
from pathlib import Path


def load(source: str | Path | bytes) -> str:
    from openpyxl import load_workbook

    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    sheets = []
    for sheet in book.worksheets:
        rows = [
            " | ".join("" if cell is None else str(cell) for cell in row)
            for row in sheet.iter_rows(values_only=True)
        ]
        sheets.append(f"# {sheet.title}\n\n" + "\n".join(rows))
    return "\n\n".join(sheets)
