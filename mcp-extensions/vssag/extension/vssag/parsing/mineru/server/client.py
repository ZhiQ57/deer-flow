"""Async MinerU-Server API client."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx

from ....errors import ParseError
from ....vssag_schemas.parse.document import ParsedDocument
from ..schemas import MinerUResult
from ..utils.content_list import content_list_to_document
from ..utils.heading_restore import restore_heading_levels
from ..utils.markdown_normalize import normalize_markdown


class MinerUServerClient:
    def __init__(
        self,
        api_url: str,
        *,
        timeout: float = 1800,
        backend: str = "pipeline",
        server_url: str = "",
        poll_interval: float = 2.0,
        max_polls: int | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.backend = backend
        self.server_url = server_url
        self.poll_interval = poll_interval
        self.max_polls = max_polls or max(1, int(timeout / max(poll_interval, 0.1)))
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def submit(self, source: str | os.PathLike[str] | bytes, *, filename: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_url:
            raise ParseError("VSSAG_MINERU_API_URL is not configured")
        options = options or {}
        content = _read_bytes(source)
        data = {
            "backend": options.get("backend", self.backend),
            "lang_list": options.get("lang_list", "ch"),
            "parse_method": options.get("parse_method", "auto"),
            "formula_enable": options.get("formula_enable", True),
            "table_enable": options.get("table_enable", True),
            "image_analysis": options.get("image_analysis", True),
            "return_md": True,
            "return_content_list": True,
            "response_format_zip": False,
        }
        if self.server_url:
            data["server_url"] = self.server_url
        attempts = max(1, int(options.get("max_retries", 3)))
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.post(
                    f"{self.api_url}/tasks",
                    files={"files": (filename, content)},
                    data=data,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(2**attempt, 8))
        raise ParseError(f"MinerU submit failed: {last_error}") from last_error

    async def wait(self, task_id: str) -> dict[str, Any]:
        for _ in range(self.max_polls):
            try:
                response = await self._client.get(f"{self.api_url}/tasks/{task_id}")
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError as exc:
                raise ParseError(f"MinerU status failed: {exc}") from exc
            status = str(payload.get("status", "")).lower()
            if status in {"completed", "complete", "success", "failed", "error"}:
                if status in {"failed", "error"}:
                    raise ParseError(payload.get("error") or "MinerU task failed")
                return payload
            await asyncio.sleep(self.poll_interval)
        raise ParseError(f"MinerU task timed out after {self.timeout:g}s")

    async def result(self, task_id: str) -> MinerUResult:
        try:
            response = await self._client.get(f"{self.api_url}/tasks/{task_id}/result")
            response.raise_for_status()
            payload = response.json()
            return MinerUResult.model_validate(payload)
        except (httpx.HTTPError, ValueError) as exc:
            raise ParseError(f"MinerU result failed: {exc}") from exc

    async def parse(
        self,
        source: str | os.PathLike[str] | bytes,
        *,
        filename: str,
        options: dict[str, Any] | None = None,
    ) -> ParsedDocument:
        task = await self.submit(source, filename=filename, options=options)
        task_id = task.get("task_id")
        if not task_id:
            raise ParseError("MinerU response did not include task_id")
        await self.wait(task_id)
        result = await self.result(task_id)
        file_result = next(iter(result.results.values()), None)
        if file_result is None:
            raise ParseError("MinerU returned no file result")
        markdown = normalize_markdown(file_result.md_content or "")
        markdown = await restore_heading_levels(markdown)
        if file_result.content_list:
            try:
                items = file_result.content_list
                if isinstance(items, str):
                    items = json.loads(items)
                return content_list_to_document(
                    filename,
                    items,
                    markdown=markdown,
                    parser="mineru-server",
                    metadata={"backend": result.backend, "version": result.version},
                )
            except Exception:
                pass
        from ..chunking.sections import build_sections

        return build_sections(filename, markdown, parser="mineru-server")


def _read_bytes(source: str | os.PathLike[str] | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    path = Path(source)
    if path.exists():
        return path.read_bytes()
    return str(source).encode("utf-8")
