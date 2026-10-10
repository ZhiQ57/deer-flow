"""VSSAG 虚拟目录路径解析。"""

from __future__ import annotations

import re


def vssag_root(
    mount_root: str | None = None,
    *,
    root_prefix: str = "/home/vssag",
    mount_name: str = "vssag",
) -> str:
    """解析 VSSAG 文档虚拟目录根路径。

    Args:
        mount_root: 外部应用提供的虚拟目录父路径。
        root_prefix: 未提供外部父路径时使用的根路径前缀。
        mount_name: 提供外部父路径时拼接的 VSSAG 目录名。

    Returns:
        不包含用户标识的公开 VSSAG 根路径。

    Raises:
        ValueError: 根路径或目录名不合法。
    """

    if mount_root is None:
        return _normalize_root(root_prefix)
    return (
        f"{_normalize_root(mount_root)}/"
        f"{_path_component(mount_name, 'mount_name')}"
    )


def vssag_private_root(public_root: str, user_label: str) -> str:
    """将公开根目录扩展为 SDK 内部的用户隔离根目录。

    Args:
        public_root: 模型可见的 VSSAG 根目录。
        user_label: SDK 从访问上下文生成的不透明用户标签。

    Returns:
        仅在 SDK 内部使用的隔离目录根路径。

    Raises:
        ValueError: 路径或标签不合法。
    """

    visible_root = _normalize_root(public_root)
    parent, mount_name = visible_root.rsplit("/", 1)
    if not mount_name:
        raise ValueError("VFS 公开根目录必须包含挂载目录名")
    label = _path_component(user_label, "user_label")
    mount = _path_component(mount_name, "mount_name")
    return f"{parent}/{label}/{mount}" if parent else f"/{label}/{mount}"


def public_path(path: str, private_root: str, root: str) -> str:
    """将内部隔离路径转换为模型可见路径。"""

    normalized = normalize_path(path)
    private = _normalize_root(private_root)
    visible = _normalize_root(root)
    if normalized == private:
        return visible
    if normalized.startswith(private + "/"):
        return visible + normalized[len(private) :]
    raise ValueError("VFS 内部路径不在当前用户隔离目录内")


def private_path(path: str, root: str, private_root: str) -> str:
    """将模型输入的公开路径转换为内部隔离路径。"""

    normalized = normalize_path(path)
    visible = _normalize_root(root)
    private = _normalize_root(private_root)
    if normalized == visible:
        return private
    if normalized.startswith(visible + "/"):
        return private + normalized[len(visible) :]
    raise ValueError("path is outside the current VSSAG scope")


def vssag_sql_root(
    mount_root: str | None = None,
    *,
    root_prefix: str = "/home/vssag",
    mount_name: str = "vssag",
) -> str:
    """解析 VSSAG SQL 虚拟目录根路径。

    Args:
        mount_root: 外部应用提供的虚拟目录父路径。
        root_prefix: 未提供外部父路径时使用的根路径前缀。
        mount_name: 提供外部父路径时拼接的 VSSAG 目录名。

    Returns:
        当前 scope 对应的 VSSAG SQL 根路径。

    Raises:
        ValueError: 根路径或目录名不合法。
    """

    return f"{vssag_root(mount_root, root_prefix=root_prefix, mount_name=mount_name)}/sql"


def normalize_path(path: str) -> str:
    """规范化不允许越界折叠的虚拟路径。"""

    normalized = re.sub(r"/+", "/", path or "/").rstrip("/") or "/"
    parts: list[str] = []
    for part in normalized.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


def _normalize_root(value: str) -> str:
    raw = str(value).strip().replace("\\", "/")
    if not raw.startswith("/"):
        raise ValueError("VFS mount_root 必须是绝对 POSIX 路径")
    if any(char in raw for char in "*?[]"):
        raise ValueError("VFS mount_root 不允许包含通配符")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError("VFS mount_root 不允许包含 ..")
        parts.append(part)
    return "/" + "/".join(parts) if parts else "/"


def _path_component(value: str, name: str) -> str:
    component = str(value).strip()
    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or any(char in component for char in "*?[]")
    ):
        raise ValueError(f"VFS {name} 必须是单个安全路径组件")
    return component
