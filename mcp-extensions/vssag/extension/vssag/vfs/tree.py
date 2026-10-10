"""VSSAG 虚拟目录树渲染。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from .paths import normalize_path


def render_tree(
    files: Mapping[str, str],
    root: str,
    *,
    max_entries: int = 5,
    max_depth: int | None = None,
) -> str:
    """将虚拟文件映射渲染为受限展开的 tree 文本。

    Args:
        files: 虚拟文件路径和内容映射。
        root: 本次展示的完整公开目录路径，不包含内部用户隔离标签。
        max_entries: 每个目录最多展示的直接子项数量。
        max_depth: 相对于根目录的最大展开深度。

    Returns:
        代码树格式的目录文本。

    Raises:
        ValueError: 展开数量或深度参数不合法。
    """

    if max_entries < 1:
        raise ValueError("tree max_entries 必须大于 0")
    if max_depth is not None and max_depth < 0:
        raise ValueError("tree max_depth 不能小于 0")

    target = normalize_path(root)
    file_paths = {normalize_path(path) for path in files}
    directories: set[str] = {target}
    children: defaultdict[str, set[str]] = defaultdict(set)

    for file_path in sorted(file_paths):
        if target != "/" and not (
            file_path == target or file_path.startswith(target + "/")
        ):
            continue
        relative = (
            file_path.lstrip("/")
            if target == "/"
            else file_path[len(target) :].lstrip("/")
        )
        if not relative:
            continue
        parent = target
        parts = relative.split("/")
        for index, part in enumerate(parts):
            child = f"{parent.rstrip('/')}/{part}" or "/"
            children[parent].add(child)
            if index < len(parts) - 1:
                directories.add(child)
            parent = child

    if target not in directories and target not in file_paths:
        return ""
    if target in file_paths and target not in directories:
        return f"{target.rsplit('/', 1)[-1]}\n"

    lines = [target.rstrip("/") + "/"]

    def walk(parent: str, prefix: str, depth: int) -> None:
        entries = sorted(
            children.get(parent, ()),
            key=lambda item: (
                item not in directories,
                item.rsplit("/", 1)[-1].casefold(),
            ),
        )
        shown = entries[:max_entries]
        for index, child in enumerate(shown):
            is_last = index == len(shown) - 1 and len(entries) <= max_entries
            lines.append(
                f"{prefix}{'└──' if is_last else '├──'} "
                f"{_name(child)}{'/' if child in directories else ''}"
            )
            if (
                child in directories
                and (max_depth is None or depth + 1 < max_depth)
            ):
                walk(
                    child,
                    prefix + ("    " if is_last else "│   "),
                    depth + 1,
                )
        if len(entries) > max_entries:
            summary = _summary(len(entries), entries, directories)
            lines.append(f"{prefix}└── ...({summary}, showing {len(shown)})")

    if max_depth != 0:
        walk(target, "", 0)
    return "\n".join(lines) + "\n"


def _name(path: str) -> str:
    return "/" if path == "/" else path.rsplit("/", 1)[-1]


def _summary(
    total: int,
    entries: list[str],
    directories: set[str],
) -> str:
    directory_count = sum(entry in directories for entry in entries)
    file_count = total - directory_count
    if directory_count == total:
        return f"{total} directories"
    if file_count == total:
        return f"{total} files"
    return f"{total} entries"
