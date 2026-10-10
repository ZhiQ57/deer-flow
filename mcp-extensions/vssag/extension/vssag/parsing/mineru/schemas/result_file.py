from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class MinerUResultFile(BaseModel):
    """One file result returned by MinerU."""

    md_content: str = ""
    content_list: Any = None
    middle_json: Any = None
    model_output: Any = None
