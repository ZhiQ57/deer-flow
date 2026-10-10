"""Unified document parsing pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import VSSAGConfig
from ..errors import ParseError
from ..vssag_schemas.parse.document import ParsedDocument
from .mineru.server.client import MinerUServerClient
from .mineru.utils.content_list import content_list_to_document
from .mineru.utils.heading_restore import restore_heading_levels
from .mineru.utils.markdown_normalize import normalize_markdown
from .mineru.chunking.sections import build_sections
from .mineru.loaders import (
    load_docx,
    load_excel,
    load_markdown,
    load_pdf,
    load_pptx,
)


class DocumentParser:
    """Parse supported files into a stable ParsedDocument contract."""

    def __init__(self, config: VSSAGConfig | None = None, llm=None):
        self.config = config or VSSAGConfig.from_env()
        self.llm = llm

    async def parse(
        self,
        source: str | os.PathLike[str] | bytes,
        *,
        filename: str | None = None,
        parser: str = "auto",
        options: dict[str, Any] | None = None,
    ) -> ParsedDocument:
        options = options or {}
        path = Path(source) if isinstance(source, (str, os.PathLike)) else None
        name = filename or (path.name if path else "document")
        extension = Path(name).suffix.lower()
        selected = parser.lower()
        if selected == "auto":
            selected = "markdown" if extension in {".md", ".markdown", ".txt"} else "local"
            if self.config.mineru_mode == "server" and extension in {".pdf", ".docx", ".pptx", ".xlsx"}:
                selected = "mineru-server" if self.config.mineru_api_url else "local"

        if selected == "mineru-server":
            client = MinerUServerClient(
                self.config.mineru_api_url,
                timeout=self.config.mineru_timeout,
                backend=self.config.mineru_backend,
                server_url=self.config.mineru_server_url,
                poll_interval=self.config.mineru_poll_interval,
            )
            try:
                return await client.parse(source, filename=name, options=options)
            finally:
                await client.close()

        raw = self._read_source(source, filename=filename)
        if selected in {"markdown", "local"}:
            markdown = self._local_to_markdown(raw, extension)
        else:
            raise ParseError(f"unsupported parser: {parser}")
        markdown = normalize_markdown(markdown)
        markdown = await restore_heading_levels(markdown, llm=self.llm)
        return build_sections(name, markdown, parser=selected)

    @staticmethod
    def _read_source(
        source: str | os.PathLike[str] | bytes,
        *,
        filename: str | None = None,
    ) -> bytes:
        if isinstance(source, bytes):
            return source
        if isinstance(source, str) and filename and (
            "\n" in source
            or source.lstrip().startswith(("#", "```", "[", "!["))
        ):
            return source.encode("utf-8")
        try:
            path = Path(source)
            if path.exists():
                return path.read_bytes()
            if isinstance(source, str) and filename:
                return source.encode("utf-8")
            raise FileNotFoundError(source)
        except OSError as exc:
            raise ParseError(f"unable to read source: {exc}") from exc

    @staticmethod
    def _local_to_markdown(raw: bytes, extension: str) -> str:
        if extension in {".md", ".markdown", ".txt", ""}:
            return load_markdown(raw)
        if extension == ".pdf":
            try:
                return load_pdf(raw)
            except Exception as exc:
                raise ParseError(f"local PDF parsing failed: {exc}") from exc
        if extension == ".docx":
            try:
                return load_docx(raw)
            except Exception as exc:
                raise ParseError(f"local DOCX parsing failed: {exc}") from exc
        if extension == ".xlsx":
            try:
                return load_excel(raw)
            except Exception as exc:
                raise ParseError(f"local XLSX parsing failed: {exc}") from exc
        if extension == ".pptx":
            try:
                return load_pptx(raw)
            except Exception as exc:
                raise ParseError(f"local PPTX parsing failed: {exc}") from exc
        raise ParseError(f"unsupported local extension: {extension}")


async def parse_document(
    source: str | os.PathLike[str] | bytes,
    *,
    filename: str | None = None,
    parser: str = "auto",
    config: VSSAGConfig | None = None,
    options: dict[str, Any] | None = None,
) -> ParsedDocument:
    return await DocumentParser(config=config).parse(
        source,
        filename=filename,
        parser=parser,
        options=options,
    )
