from __future__ import annotations

from pydantic import BaseModel

from .document import ParsedDocument


class ParseResult(BaseModel):
    ok: bool
    document: ParsedDocument | None = None
    error: str | None = None
