from __future__ import annotations

import fnmatch
import re

from ..vfbuild.builder import VirtualFileSystemBuilder
from ...vssag_schemas.vfs.request import VFSRequest
from ...vssag_schemas.vfs.response import VFSResponse
from ..paths import private_path


class ToolUseRestorer:
    """Restore structured grep_file/tool calls to virtual file operations."""

    def __init__(self, builder: VirtualFileSystemBuilder, config):
        self.builder = builder
        self.config = config

    async def restore(self, request: VFSRequest) -> VFSResponse:
        if _has_parent_segment(request.path):
            return VFSResponse(
                ok=False,
                mode=request.mode,
                path=request.path,
                error="path traversal is not allowed",
            )
        path = _normalize(request.path)
        root = self.builder.root(request.context, request.mount_root)
        if path == "/":
            path = root
        if path != root and not path.startswith(root + "/"):
            return VFSResponse(
                ok=False,
                mode=request.mode,
                path=path,
                error="path is outside the current user scope",
            )
        files = await self.builder.build_private(
            request.context,
            request.mount_root,
        )
        private_root = self.builder.private_root(
            request.context,
            request.mount_root,
        )
        try:
            internal_path = private_path(path, root, private_root)
        except ValueError:
            return VFSResponse(
                ok=False,
                mode=request.mode,
                path=path,
                error="path is outside the current user scope",
            )
        if request.mode == "dirs":
            entries = await self.builder.entries(
                request.context,
                path,
                mount_root=request.mount_root,
            )
            if request.value:
                entries = [entry for entry in entries if fnmatch.fnmatch(entry.name, request.value)]
            return VFSResponse(
                mode=request.mode,
                path=path,
                entries=entries,
                source_ids=_source_ids(path, files),
            )
        content = files.get(internal_path)
        if content is None:
            return VFSResponse(ok=False, mode=request.mode, path=path, error="file not found")
        if request.mode == "full":
            return VFSResponse(
                mode=request.mode,
                path=path,
                content=content[: self.config.max_full_chars],
                source_ids=_source_ids(path, files),
            )
        if request.mode == "range":
            lines = content.splitlines()
            start, end = _parse_range(request.value or "")
            selected = lines[start - 1 : end]
            content = "\n".join(selected)
            if content:
                content += "\n"
            return VFSResponse(
                mode=request.mode,
                path=path,
                content=content[: self.config.max_full_chars],
                source_ids=_source_ids(path, files),
            )
        if request.mode == "lines":
            pattern = request.value or ""
            data = [
                {"line": index, "text": line}
                for index, line in enumerate(content.splitlines(), start=1)
                if not pattern or _matches(pattern, line)
            ][: self.config.max_grep_results]
            return VFSResponse(
                mode=request.mode,
                path=path,
                data=data,
                content="\n".join(item["text"] for item in data),
                source_ids=_source_ids(path, files),
            )
        if request.mode == "heading":
            pattern = (request.value or "").lower()
            data = []
            for index, line in enumerate(content.splitlines(), start=1):
                match = re.match(r"^(#{1,6})\s+(.+)$", line)
                if match and (not pattern or pattern in match.group(2).lower()):
                    data.append({"level": len(match.group(1)), "text": line, "line": index, "anchor": match.group(2).strip()})
            return VFSResponse(
                mode=request.mode,
                path=path,
                data=data,
                source_ids=_source_ids(path, files),
            )
        if request.mode == "block":
            pattern = (request.value or "").lower()
            data = []
            for index, line in enumerate(content.splitlines(), start=1):
                kind = "image" if line.startswith("![") else "table" if "|" in line else "code" if line.startswith("```") else ""
                if kind and (not pattern or pattern in line.lower() or pattern in kind):
                    data.append({"block_id": f"{path}:{index}", "type": kind, "summary": line[:300], "order": index})
            return VFSResponse(
                mode=request.mode,
                path=path,
                data=data,
                source_ids=_source_ids(path, files),
            )
        return VFSResponse(ok=False, mode=request.mode, path=path, error="unsupported mode")


def _normalize(path: str) -> str:
    path = re.sub(r"/+", "/", path or "/").rstrip("/") or "/"
    parts = []
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


def _has_parent_segment(path: str) -> bool:
    return any(part == ".." for part in (path or "").replace("\\", "/").split("/"))


def _parse_range(value: str) -> tuple[int, int]:
    if re.fullmatch(r"\d+", value):
        number = int(value)
        return number, number
    match = re.fullmatch(r"(\d*)-(\d*)", value)
    if not match:
        return 1, 1
    start = int(match.group(1) or 1)
    end = int(match.group(2) or 10_000_000)
    return max(1, start), max(start, end)


def _matches(pattern: str, line: str) -> bool:
    try:
        return re.search(pattern, line, flags=re.IGNORECASE) is not None
    except re.error:
        return pattern.lower() in line.lower()


def _source_ids(path: str, files: dict[str, str]) -> list[str]:
    """Extract source ids from canonical own/workspace/session paths."""
    values: set[str] = set()
    for candidate in [path, *files]:
        parts = [part for part in candidate.split("/") if part]
        for scope_name in ("own", "sessions"):
            if scope_name in parts:
                index = parts.index(scope_name)
                if index + 1 < len(parts):
                    values.add(parts[index + 1])
        if "workspace" in parts:
            index = parts.index("workspace")
            # workspace/{workspace-slug}/{source-id}/...
            if index + 2 < len(parts):
                values.add(parts[index + 2])
    return sorted(values)
