"""Build an ephemeral, permission-pruned virtual file tree."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from ...vssag_schemas.vfs.entry import VFSEntry
from ...vssag_schemas.vfs.access_context import VSSAGAccessContext
from ..paths import public_path, vssag_private_root, vssag_root
from ..tree import render_tree


class VirtualFileSystemBuilder:
    def __init__(
        self,
        store,
        root_prefix: str = "/home/vssag",
        mount_name: str = "vssag",
    ):
        self.store = store
        self.root_prefix = root_prefix
        self.mount_name = mount_name

    def root(
        self,
        context: VSSAGAccessContext,
        mount_root: str | None = None,
    ) -> str:
        """返回模型可见的文档虚拟目录根路径。"""

        return vssag_root(
            mount_root,
            root_prefix=self.root_prefix,
            mount_name=self.mount_name,
        )

    def private_root(
        self,
        context: VSSAGAccessContext,
        mount_root: str | None = None,
    ) -> str:
        """返回仅供 SDK 使用的用户隔离根路径。"""

        return vssag_private_root(self.root(context, mount_root), context.user_label)

    async def build_private(
        self,
        context: VSSAGAccessContext,
        mount_root: str | None = None,
    ) -> dict[str, str]:
        """构建包含内部隔离路径的权限裁剪文件映射。"""

        records = await self.store.list_records(context.allowed_source_ids())
        records = [
            record
            for record in records
            if _visible(record, context)
        ]
        grouped: defaultdict[str, list] = defaultdict(list)
        for record in records:
            grouped[record.source_id].append(record)
        root = self.private_root(context, mount_root)
        files = {
            f"{root}/index.md": self._root_index(grouped),
        }
        for source_id, source_records in grouped.items():
            source_records.sort(key=lambda record: (record.position, record.record_id))
            title = next((record.title for record in source_records if record.title), source_id)
            first = source_records[0] if source_records else None
            scope_type = str((first.metadata if first else {}).get("scope_type", "own")).lower()
            if scope_type not in {"own", "workspace", "sessions"}:
                scope_type = "own"
            if scope_type == "workspace":
                workspace = str((first.metadata if first else {}).get("workspace_slug", "workspace"))
                source_root = f"{root}/workspace/{workspace}/{source_id}"
            else:
                source_root = f"{root}/{scope_type}/{source_id}"
            body = "\n\n".join(record.text for record in source_records if record.text)
            files[f"{source_root}/index.md"] = f"# {title}\n\n{source_records[0].summary if source_records else ''}\n"
            files[f"{source_root}/md.md"] = body + ("\n" if body else "")
            files[f"{source_root}/logs.md"] = "VSSAG source history is provided by the host application.\n"
            for record in source_records:
                block_path = f"{source_root}/blocks/{record.record_id}.md"
                files[block_path] = (record.text or "") + "\n"
            metadata = first.metadata if first else {}
            raw_content = metadata.get("raw_content")
            raw_filename = metadata.get("raw_filename") or metadata.get("filename")
            if isinstance(raw_content, str) and raw_content:
                raw_name = _safe_filename(raw_filename or "raw.txt")
                files[f"{source_root}/{raw_name}"] = raw_content
            for name, content in (metadata.get("virtual_files") or {}).items():
                if isinstance(name, str) and isinstance(content, str):
                    files[f"{source_root}/{_safe_filename(name)}"] = content
        return files

    async def build(
        self,
        context: VSSAGAccessContext,
        mount_root: str | None = None,
    ) -> dict[str, str]:
        """构建模型可见的权限裁剪文件映射。"""

        root = self.root(context, mount_root)
        private_root = self.private_root(context, mount_root)
        files = await self.build_private(context, mount_root)
        return {
            public_path(path, private_root, root): content
            for path, content in files.items()
        }

    async def build_tree(
        self,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
        path: str | None = None,
        max_depth: int | None = None,
    ) -> str:
        """构建受限展开的文档虚拟目录树。"""

        files = await self.build(context, mount_root)
        target = self.root(context, mount_root) if path in {None, "", "/"} else path
        return render_tree(files, target, max_depth=max_depth)

    async def entries(
        self,
        context: VSSAGAccessContext,
        path: str = "/",
        *,
        mount_root: str | None = None,
    ) -> list[VFSEntry]:
        files = await self.build(context, mount_root)
        normalized = path.rstrip("/") or "/"
        seen: dict[str, VFSEntry] = {}
        for file_path, content in files.items():
            if normalized != "/" and not file_path.startswith(normalized.rstrip("/") + "/"):
                continue
            rest = file_path.lstrip("/")
            prefix = normalized.rstrip("/") + "/" if normalized != "/" else "/"
            relative = file_path[len(prefix):] if file_path.startswith(prefix) else rest
            name = relative.split("/", 1)[0]
            child = (normalized.rstrip("/") + "/" + name).rstrip("/") if normalized != "/" else "/" + name
            if "/" in relative:
                seen[child] = VFSEntry(name=name, path=child, type="dir")
            else:
                seen[child] = VFSEntry(name=name, path=child, type="file", size=len(content))
        return sorted(seen.values(), key=lambda item: (item.type != "dir", item.name))

    @staticmethod
    def _root_index(grouped) -> str:
        lines = ["# Knowledge Base", ""]
        for source_id, records in sorted(grouped.items()):
            title = next((record.title for record in records if record.title), source_id)
            lines.append(f"- {title} ({source_id})")
        return "\n".join(lines) + "\n"


def _visible(record, context: VSSAGAccessContext) -> bool:
    metadata = record.metadata or {}
    return context.allows_record(record.source_id, metadata)


def _safe_filename(value: str) -> str:
    name = PurePosixPath(str(value).replace("\\", "/")).name
    return name or "raw.txt"
