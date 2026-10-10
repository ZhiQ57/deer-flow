"""Tool Use and restricted Bash restoration for the SQL virtual directory."""

from __future__ import annotations

import fnmatch
import re
import shlex

from ...config import SQLAbilityConfig
from ...indexing.index_stores.sql_schema_store import SQLSchemaStore
from ...vssag_schemas.sql import SQLScope, SQLToolRequest, SQLToolResponse
from ..vfbuild.sql import SQLVirtualFileSystemBuilder


class SQLToolUseRestorer:
    def __init__(self, builder: SQLVirtualFileSystemBuilder, store: SQLSchemaStore):
        self.builder = builder
        self.store = store

    async def restore(self, request: SQLToolRequest) -> SQLToolResponse:
        """将结构化请求还原为当前 SQL 文件视图中的操作。

        Args:
            request: 包含操作模式、完整公开路径和访问范围的请求。

        Returns:
            保留公开路径的目录或文件响应；越界和缺失文件返回错误。
        """

        if _has_parent_segment(request.path):
            return SQLToolResponse(
                ok=False,
                mode=request.mode,
                path=request.path,
                error="path traversal is not allowed",
            )
        files = await self.builder.build(request.scope)
        path = _resolve(request.path, "/")
        root = self.builder.root(request.scope)
        parent = root.rsplit("/", 1)[0]
        if path == "/":
            path = parent
        if path not in {parent, root} and not path.startswith(root + "/"):
            return SQLToolResponse(
                ok=False,
                mode=request.mode,
                path=path,
                error="path is outside the SQL scope",
            )
        if request.mode == "tables":
            entries = await self.builder.entries(request.scope, path)
            if request.value:
                entries = [
                    item
                    for item in entries
                    if fnmatch.fnmatch(item["name"], request.value)
                ]
            return SQLToolResponse(
                mode=request.mode,
                path=path,
                entries=entries,
            )
        if request.mode == "ddls":
            content = files.get(path)
            if content is None:
                return SQLToolResponse(
                    ok=False, mode=request.mode, path=path, error="file not found"
                )
            return SQLToolResponse(mode=request.mode, path=path, content=content)
        if request.mode == "graph":
            if path in files:
                return SQLToolResponse(
                    mode=request.mode, path=path, content=files[path]
                )
            table_name = request.value or _table_from_path(path)
            if table_name:
                table = await self.store.get_table(table_name)
                if table is None or not self.builder._visible(table, request.scope):
                    return SQLToolResponse(
                        ok=False,
                        mode=request.mode,
                        path=path,
                        error="table is outside the SQL scope",
                    )
            content = await self._graph_content(table_name, request.scope)
            return SQLToolResponse(
                mode=request.mode,
                path=path,
                content=content,
            )
        if request.mode == "values":
            table_name = _table_from_path(path)
            values = await self.store.search_values(
                request.value or "",
                table_name=table_name if table_name not in {None, "sql"} else None,
                limit=100,
            )
            visible_values = []
            for value, score in values:
                table = await self.store.get_table(value.raw_table_name)
                if (
                    table is not None
                    and self.builder._visible(table, request.scope)
                    and request.scope.allows_column(
                        value.raw_table_name,
                        value.column_name,
                        value.random_table_name,
                    )
                ):
                    visible_values.append((value, score))
            values = visible_values
            data = [
                {
                    "value": value.raw_value,
                    "table": (
                        value.random_table_name
                        if not self.builder.config.security.allow_real_table_names
                        else value.raw_table_name
                    ),
                    "column": (
                        value.column_name
                    ),
                    "score": score,
                }
                for value, score in values
            ]
            return SQLToolResponse(
                mode=request.mode,
                path=path,
                data=data,
                content="\n".join(
                    f"{item['table']}.{item['column']} = {item['value']}"
                    for item in data
                ),
            )
        if request.mode == "evidence":
            evidence = await self.store.search_evidence(request.value or "", limit=100)
            all_tables = await self.store.list_tables()
            hidden_names = {
                name
                for table in all_tables
                if not self.builder._visible(table, request.scope)
                for name in (table.raw_table_name, table.random_table_name)
            }
            evidence = [
                (item, score)
                for item, score in evidence
                if not hidden_names.intersection(
                    f"{item.retrieval_text}\n{item.evidence_content}"
                )
            ]
            data = [
                {
                    "triggers": item.triggers,
                    "content": item.evidence_content,
                    "type": item.evidence_type,
                    "score": score,
                }
                for item, score in evidence
            ]
            content = "\n\n".join(item["content"] for item in data)
            return SQLToolResponse(
                mode=request.mode,
                path=path,
                data=data,
                content=await self.builder.mask_text(content),
            )
        return SQLToolResponse(
            ok=False,
            mode=request.mode,
            path=path,
            error="unsupported mode",
        )

    async def _graph_content(
        self,
        table_name: str | None,
        scope: SQLScope | None = None,
    ) -> str:
        if not table_name:
            edges = await self.store.list_graph_edges(
                None,
                include_candidates=self.builder.config.retriever_service.index.graph.include_candidates,
                limit=1000,
            )
        else:
            edges = await self.store.graph_neighbors(
                table_name,
                max_hops=self.builder.config.retriever_service.max_join_hops,
                include_candidates=self.builder.config.retriever_service.index.graph.include_candidates,
                limit=100,
            )
        if scope is not None:
            edges = [
                edge
                for edge in edges
                if scope.allows_table(
                    edge.raw_schema_name,
                    edge.raw_table_name,
                    edge.random_table_name,
                )
                and scope.allows_table(
                    edge.related_raw_schema_name,
                    edge.related_raw_table_name,
                    edge.related_random_table_name,
                )
                and scope.allows_column(
                    edge.raw_table_name,
                    edge.raw_column_name or "",
                    edge.random_table_name,
                )
                and scope.allows_column(
                    edge.related_raw_table_name,
                    edge.related_raw_column_name or "",
                    edge.related_random_table_name,
                )
            ]
        lines = ["# 表关联关系", ""]
        for edge in edges:
            left = (
                edge.random_table_name
                if not self.builder.config.security.allow_real_table_names
                else edge.raw_table_name
            )
            right = (
                edge.related_random_table_name
                if not self.builder.config.security.allow_real_table_names
                else edge.related_raw_table_name
            )
            join_condition = edge.join_condition or "未提供 JOIN 条件"
            if not self.builder.config.security.allow_real_table_names:
                join_condition = await self.builder.mask_text(join_condition)
            lines.append(
                f"- {left} -> {right}: {join_condition} "
                f"({edge.relation_type}, {edge.evidence_source}, "
                f"confidence={edge.confidence:.4f})"
            )
        return await self.builder.mask_text("\n".join(lines) + "\n")

class SQLBashRestorer:
    def __init__(self, tool_use: SQLToolUseRestorer):
        self.tool_use = tool_use
        self.cwd: dict[tuple[str, str | None], str] = {}

    async def execute(self, command: str, scope: SQLScope) -> SQLToolResponse:
        """执行受限 SQL 虚拟目录导航、读取与检索指令。

        Args:
            command: 模型使用公开路径生成的 Bash 指令。
            scope: 调用方授予的 SQL 访问范围和外部挂载父路径。

        Returns:
            含终端文本的响应；路径越界或指令不受支持时返回错误。
        """

        command = (command or "").strip()
        if not command:
            return SQLToolResponse(ok=False, error="empty command")
        if any(token in command for token in (">", "<", "`", "$(", "${")):
            return SQLToolResponse(ok=False, error="redirection and command substitution are not allowed")
        if ";" in command or "&&" in command:
            result = SQLToolResponse()
            for part in re.split(r";|&&", command):
                if part.strip():
                    result = await self.execute(part.strip(), scope)
                    if not result.ok:
                        return result
            return result
        if "|" in command:
            left, right = [part.strip() for part in command.split("|", 1)]
            first = await self.execute(left, scope)
            if not first.ok:
                return first
            args = shlex.split(right, posix=True)
            if args and args[0] in {"head", "tail"}:
                count = _head_count(args)
                lines = first.content.splitlines()
                selected = lines[:count] if args[0] == "head" else lines[-count:]
                first.content = "\n".join(selected) + ("\n" if selected else "")
            return first
        try:
            args = shlex.split(command, posix=True)
        except ValueError as exc:
            return SQLToolResponse(ok=False, error=str(exc))
        if not args:
            return SQLToolResponse()
        name, values = args[0], args[1:]
        cwd_key = (scope.user_id, scope.mount_root)
        root = self.tool_use.builder.root(scope)
        parent = root.rsplit("/", 1)[0]
        cwd = self.cwd.get(cwd_key, parent)
        if name == "pwd":
            return SQLToolResponse(content=cwd + "\n")
        if name == "cd":
            target = values[0] if values else parent
            if _has_parent_segment(target):
                return SQLToolResponse(ok=False, error="path traversal is not allowed")
            resolved = _resolve(target, cwd)
            if resolved != parent and not _within_root(resolved, root):
                return SQLToolResponse(ok=False, error="path is outside the SQL scope")
            self.cwd[cwd_key] = resolved
            return SQLToolResponse()
        if name == "tree":
            raw_path = "."
            skip_next = False
            for value in values:
                if skip_next:
                    skip_next = False
                    continue
                if value == "-L":
                    skip_next = True
                    continue
                if value.startswith("-"):
                    continue
                raw_path = value
                break
            if _has_parent_segment(raw_path):
                return SQLToolResponse(ok=False, error="path traversal is not allowed")
            path = _resolve(raw_path, cwd)
            if path == "/":
                path = parent
            if path != parent and not _within_root(path, root):
                return SQLToolResponse(
                    ok=False,
                    error="path is outside the SQL scope",
                )
            return SQLToolResponse(
                mode="tables",
                path=path,
                content=await self.tool_use.builder.build_tree(
                    scope,
                    path=path,
                    max_depth=_tree_depth(values),
                ),
            )
        if name == "ls":
            raw_path = next((value for value in values if not value.startswith("-")), ".")
            if _has_parent_segment(raw_path):
                return SQLToolResponse(ok=False, error="path traversal is not allowed")
            response = await self.tool_use.restore(
                SQLToolRequest(
                    mode="tables",
                    path=_resolve(raw_path, cwd),
                    scope=scope,
                )
            )
            if response.ok:
                response.content = "".join(
                    f"{entry.name}{'/' if entry.type == 'dir' else ''}\n"
                    for entry in response.entries
                )
            return response
        if name in {"cat", "head", "tail"}:
            raw_path = next((value for value in values if not value.startswith("-")), "")
            if _has_parent_segment(raw_path):
                return SQLToolResponse(ok=False, error="path traversal is not allowed")
            response = await self.tool_use.restore(
                SQLToolRequest(
                    mode="ddls",
                    path=_resolve(raw_path, cwd),
                    scope=scope,
                )
            )
            if name in {"head", "tail"} and response.ok:
                count = _head_count(values)
                lines = response.content.splitlines()
                selected = lines[:count] if name == "head" else lines[-count:]
                response.content = "\n".join(selected) + ("\n" if selected else "")
            return response
        if name in {"grep", "rg"}:
            pattern = next((value for value in values if not value.startswith("-")), "")
            raw_path = (
                next(
                    (
                        value
                        for value in values[values.index(pattern) + 1 :]
                        if not value.startswith("-")
                    ),
                    ".",
                )
                if pattern in values
                else "."
            )
            if not pattern:
                return SQLToolResponse(ok=False, error="search pattern is required")
            if _has_parent_segment(raw_path):
                return SQLToolResponse(ok=False, error="path traversal is not allowed")
            path = _resolve(raw_path, cwd)
            if path != parent and not _within_root(path, root):
                return SQLToolResponse(ok=False, error="path is outside the SQL scope")
            try:
                expression = re.compile(pattern, flags=re.IGNORECASE)
            except re.error as exc:
                return SQLToolResponse(ok=False, error=f"invalid search pattern: {exc}")
            files = await self.tool_use.builder.build(scope)
            candidates = [
                (file_path, content)
                for file_path, content in sorted(files.items())
                if file_path == path or file_path.startswith(path.rstrip("/") + "/")
            ]
            if not candidates:
                return SQLToolResponse(
                    ok=False,
                    path=path,
                    error="file or directory not found",
                )
            lines = [
                f"{file_path}:{line_number}: {line}"
                for file_path, content in candidates
                for line_number, line in enumerate(content.splitlines(), start=1)
                if expression.search(line)
            ]
            return SQLToolResponse(
                mode="ddls",
                path=path,
                content="\n".join(lines) + ("\n" if lines else ""),
            )
        if name == "find":
            raw_path = next((value for value in values if not value.startswith("-")), ".")
            if _has_parent_segment(raw_path):
                return SQLToolResponse(ok=False, error="path traversal is not allowed")
            path = _resolve(raw_path, cwd)
            if path != parent and not _within_root(path, root):
                return SQLToolResponse(ok=False, error="path is outside the SQL scope")
            pattern = "*"
            if "-name" in values and values.index("-name") + 1 < len(values):
                pattern = values[values.index("-name") + 1].strip("'\"")
            files = await self.tool_use.builder.build(scope)
            base = path.rstrip("/") + "/"
            entries = [
                {
                    "name": file_path.rsplit("/", 1)[-1],
                    "path": file_path,
                    "type": "file",
                    "size": len(content),
                }
                for file_path, content in files.items()
                if file_path.startswith(base)
                and fnmatch.fnmatch(file_path.rsplit("/", 1)[-1], pattern)
            ]
            selected = [
                item
                for item in entries
                if item["type"] == "file" and fnmatch.fnmatch(item["name"], pattern)
            ]
            return SQLToolResponse(
                mode="tables",
                path=_resolve(raw_path, cwd),
                data=selected,
                content="\n".join(item["path"] for item in selected),
            )
        return SQLToolResponse(ok=False, error=f"unsupported command: {name}")


def _resolve(path: str, cwd: str) -> str:
    if not path:
        return cwd
    resolved = path if path.startswith("/") else f"{cwd.rstrip('/')}/{path}"
    resolved = re.sub(r"/+", "/", resolved or "/").rstrip("/") or "/"
    parts: list[str] = []
    for part in resolved.split("/"):
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


def _within_root(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _table_from_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if "table" not in parts:
        return None
    index = parts.index("table")
    return parts[index + 1] if index + 1 < len(parts) else None


def _head_count(values: list[str]) -> int:
    if "-n" in values:
        index = values.index("-n")
        if index + 1 < len(values):
            return max(1, int(values[index + 1]))
    for value in values:
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
