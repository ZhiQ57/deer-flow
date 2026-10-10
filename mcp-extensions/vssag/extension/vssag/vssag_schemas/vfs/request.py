from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .access_context import VSSAGAccessContext


class VFSRequest(BaseModel):
    mode: Literal["dirs", "full", "range", "lines", "heading", "block"]
    path: str
    value: str | None = None
    context: VSSAGAccessContext
    mount_root: str | None = None
