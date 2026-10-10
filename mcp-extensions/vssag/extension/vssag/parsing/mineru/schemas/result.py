from __future__ import annotations

from pydantic import BaseModel, Field

from .result_file import MinerUResultFile


class MinerUResult(BaseModel):
    """MinerU parsing result envelope."""

    backend: str = ""
    version: str = ""
    results: dict[str, MinerUResultFile] = Field(default_factory=dict)
