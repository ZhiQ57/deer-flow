from __future__ import annotations

from pydantic import BaseModel


class MinerUTask(BaseModel):
    """MinerU asynchronous task status."""

    task_id: str
    status: str = "queued"
    error: str | None = None
    result_url: str | None = None
