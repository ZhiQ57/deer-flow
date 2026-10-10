from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .scope import SQLScope


class SQLToolRequest(BaseModel):
    """Request to restore one SQL virtual-directory operation."""

    mode: Literal["tables", "graph", "values", "ddls", "evidence"]
    value: str | None = None
    path: str
    scope: SQLScope
