"""VSSAG 文档虚拟目录的受限 Bash 还原。"""

from __future__ import annotations

import re
from typing import Any

from ...vssag_schemas.vfs.access_context import VSSAGAccessContext
from ...vssag_schemas.vfs.response import VFSResponse
from ..paths import normalize_path
from .just_bash import JustBashParser
from .tool_use import ToolUseRestorer


class VFSBashRestorer:
    """将 just-bash 解析结果翻译为 VSSAG 虚拟目录操作。"""

    def __init__(self, tool_use: ToolUseRestorer, parser: JustBashParser):
        self.tool_use = tool_use
        self.parser = parser
        self.cwd: dict[tuple[str, str | None], str] = {}

    async def execute(
        self,
        command: str,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
    ) -> VFSResponse:
        """执行一条由 just-bash 解析的受限 Bash 指令。

        Args:
            command: 模型输出的 Bash 指令。
            context: 当前调用方的 VSSAG 访问上下文。
            mount_root: 外部应用提供的虚拟目录父路径。

        Returns:
            使用终端风格文本填充 `content` 的 VFS 响应。
        """

        command = (command or "").strip()
        if not command:
            return VFSResponse(ok=False, error="empty command")
        try:
            statements = await self.parser.parse(command)
        except RuntimeError as exc:
            return VFSResponse(ok=False, error=f"bash parse error: {exc}")

        result = VFSResponse()
        for statement in statements:
            pipelines = statement.get("pipelines")
            operators = statement.get("operators")
            if not isinstance(pipelines, list) or not isinstance(operators, list):
                return VFSResponse(ok=False, error="invalid parsed Bash statement")
            for index, pipeline in enumerate(pipelines):
                if index:
                    operator = operators[index - 1]
                    if operator == "&&" and not result.ok:
                        return result
                    if operator == "||" and result.ok:
                        continue
                    if operator not in {"&&", "||"}:
                        return VFSResponse(
                            ok=False,
                            error=f"unsupported statement operator: {operator}",
                        )
                result = await self._execute_pipeline(
                    pipeline,
                    context,
                    mount_root=mount_root,
                )
        return result

    async def close(self) -> None:
        """释放 just-bash 解析 Worker。"""

        await self.parser.close()

    async def _execute_pipeline(
        self,
        pipeline: Any,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None,
    ) -> VFSResponse:
        if not isinstance(pipeline, list) or not pipeline:
            return VFSResponse(ok=False, error="empty Bash pipeline")
        result = await self._execute_command(
            pipeline[0],
            context,
            mount_root=mount_root,
        )
        for command in pipeline[1:]:
            if not result.ok:
                return result
            if not isinstance(command, dict):
                return VFSResponse(ok=False, error="invalid parsed Bash command")
            name = command.get("name")
            values = command.get("args")
            if name not in {"head", "tail"} or not isinstance(values, list):
                return VFSResponse(
                    ok=False,
                    error="only head and tail are supported after a pipe",
                )
            count = _head_count([str(value) for value in values])
            lines = result.content.splitlines()
            selected = lines[:count] if name == "head" else lines[-count:]
            result.content = "\n".join(selected) + ("\n" if selected else "")
        return result

    async def _execute_command(
        self,
        command: Any,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None,
    ) -> VFSResponse:
        if not isinstance(command, dict):
            return VFSResponse(ok=False, error="invalid parsed Bash command")
        name = command.get("name")
        values = command.get("args")
        if not isinstance(name, str) or not isinstance(values, list):
            return VFSResponse(ok=False, error="invalid parsed Bash command")
        values = [str(value) for value in values]

        root = self.tool_use.builder.root(context, mount_root)
        cwd_key = (context.user_label, mount_root)
        cwd = self.cwd.get(cwd_key, root)

        if name == "pwd":
            return VFSResponse(path=cwd, content=cwd + "\n")
        if name == "cd":
            raw_path = values[0] if values else root
            if _has_parent_segment(raw_path):
                return VFSResponse(ok=False, error="path traversal is not allowed")
            path = _resolve(raw_path, cwd)
            if not _within_root(path, root):
                return VFSResponse(
                    ok=False,
                    error="path is outside the current user scope",
                )
            self.cwd[cwd_key] = path
            return VFSResponse(path=path)
        if name == "tree":
            raw_path = _path_argument(values, ".")
            if _has_parent_segment(raw_path):
                return VFSResponse(ok=False, error="path traversal is not allowed")
            path = _resolve(raw_path, cwd)
            if not _within_root(path, root):
                return VFSResponse(
                    ok=False,
                    error="path is outside the current user scope",
                )
            content = await self.tool_use.builder.build_tree(
                context,
                mount_root=mount_root,
                path=path,
                max_depth=_tree_depth(values),
            )
            return VFSResponse(mode="dirs", path=path, content=content)
        if name == "ls":
            raw_path = _path_argument(values, ".")
            if _has_parent_segment(raw_path):
                return VFSResponse(ok=False, error="path traversal is not allowed")
            path = _resolve(raw_path, cwd)
            if not _within_root(path, root):
                return VFSResponse(
                    ok=False,
                    error="path is outside the current user scope",
                )
            entries = await self.tool_use.builder.entries(
                context,
                path,
                mount_root=mount_root,
            )
            content = "\n".join(
                f"{entry.name}{'/' if entry.type == 'dir' else ''}"
                for entry in entries
            )
            return VFSResponse(
                mode="dirs",
                path=path,
                entries=entries,
                content=content + ("\n" if content else ""),
            )
        if name in {"cat", "head", "tail", "sed"}:
            raw_path = _file_path(values)
            if not raw_path:
                return VFSResponse(ok=False, error="file path is required")
            if _has_parent_segment(raw_path):
                return VFSResponse(ok=False, error="path traversal is not allowed")
            path = _resolve(raw_path, cwd)
            if not _within_root(path, root):
                return VFSResponse(
                    ok=False,
                    error="path is outside the current user scope",
                )
            response = await self._read_file(context, path, mount_root)
            if not response.ok:
                return response
            if name in {"head", "tail"}:
                count = _head_count(values)
                lines = response.content.splitlines()
                selected = lines[:count] if name == "head" else lines[-count:]
                response.content = "\n".join(selected) + ("\n" if selected else "")
            if name == "sed":
                expression = next(
                    (
                        value
                        for value in values
                        if re.fullmatch(r"\d+(?:,\d+)?p", value)
                    ),
                    "",
                )
                match = re.fullmatch(r"(\d+)(?:,(\d+))?p", expression)
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2) or match.group(1))
                    lines = response.content.splitlines()
                    selected = lines[max(0, start - 1) : end]
                    response.content = "\n".join(selected) + (
                        "\n" if selected else ""
                    )
            return response
        if name in {"grep", "rg"}:
            pattern, raw_path = _grep_arguments(values)
            if not pattern:
                return VFSResponse(ok=False, error="search pattern is required")
            if _has_parent_segment(raw_path):
                return VFSResponse(ok=False, error="path traversal is not allowed")
            path = _resolve(raw_path, cwd)
            if not _within_root(path, root):
                return VFSResponse(
                    ok=False,
                    error="path is outside the current user scope",
                )
            files = await self.tool_use.builder.build(context, mount_root)
            candidates = (
                [(path, files[path])]
                if path in files
                else [
                    (file_path, content)
                    for file_path, content in files.items()
                    if file_path.startswith(path.rstrip("/") + "/")
                ]
            )
            data: list[dict[str, object]] = []
            for file_path, content in sorted(candidates):
                for line_number, line in enumerate(content.splitlines(), start=1):
                    if _matches(pattern, line):
                        data.append(
                            {
                                "path": file_path,
                                "line": line_number,
                                "text": line,
                            }
                        )
                    if len(data) >= self.tool_use.config.max_grep_results:
                        break
                if len(data) >= self.tool_use.config.max_grep_results:
                    break
            content = "\n".join(
                f"{item['path']}:{item['line']}: {item['text']}"
                for item in data
            )
            return VFSResponse(
                mode="lines",
                path=path,
                data=data,
                content=content + ("\n" if content else ""),
            )
        return VFSResponse(ok=False, error=f"unsupported command: {name}")

    async def _read_file(
        self,
        context: VSSAGAccessContext,
        path: str,
        mount_root: str | None,
    ) -> VFSResponse:
        """读取当前访问上下文内的一份公开文件。"""

        from ...vssag_schemas.vfs.request import VFSRequest

        return await self.tool_use.restore(
            VFSRequest(
                mode="full",
                path=path,
                context=context,
                mount_root=mount_root,
            )
        )


def _resolve(path: str, cwd: str) -> str:
    value = path if path.startswith("/") else f"{cwd.rstrip('/')}/{path}"
    return normalize_path(value)


def _has_parent_segment(path: str) -> bool:
    return any(part == ".." for part in (path or "").replace("\\", "/").split("/"))


def _within_root(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _path_argument(values: list[str], default: str) -> str:
    positional: list[str] = []
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--":
            positional.extend(values[index + 1 :])
            break
        if value == "-L":
            index += 2
            continue
        if value.startswith("-"):
            index += 1
            continue
        positional.append(value)
        index += 1
    return positional[-1] if positional else default


def _file_path(values: list[str]) -> str:
    return _path_argument(values, "")


def _grep_arguments(values: list[str]) -> tuple[str, str]:
    patterns: list[str] = []
    positional: list[str] = []
    index = 0
    options_ended = False
    while index < len(values):
        value = values[index]
        if not options_ended and value == "--":
            options_ended = True
            index += 1
            continue
        if not options_ended and value in {"-e", "--regexp"}:
            if index + 1 < len(values):
                patterns.append(values[index + 1])
                index += 2
                continue
            break
        if not options_ended and value.startswith("-"):
            index += 1
            continue
        positional.append(value)
        index += 1
    pattern = patterns[0] if patterns else (positional[0] if positional else "")
    path = positional[0] if patterns and positional else (
        positional[1] if len(positional) > 1 else "."
    )
    return pattern, path


def _head_count(values: list[str]) -> int:
    for index, value in enumerate(values):
        if value == "-n" and index + 1 < len(values) and values[index + 1].isdigit():
            return max(1, int(values[index + 1]))
        if value.startswith("-n") and value[2:].isdigit():
            return max(1, int(value[2:]))
    return 10


def _tree_depth(values: list[str]) -> int | None:
    for index, value in enumerate(values):
        candidate = None
        if value == "-L" and index + 1 < len(values):
            candidate = values[index + 1]
        elif value.startswith("-L") and value[2:]:
            candidate = value[2:]
        if candidate is not None:
            try:
                return max(0, int(candidate))
            except ValueError:
                return 0
    return None


def _matches(pattern: str, line: str) -> bool:
    try:
        return re.search(pattern, line, flags=re.IGNORECASE) is not None
    except re.error:
        return pattern.lower() in line.lower()
