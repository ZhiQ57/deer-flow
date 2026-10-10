from __future__ import annotations

from ..vfbuild.builder import VirtualFileSystemBuilder
from ...vssag_schemas.vfs.request import VFSRequest
from ...vssag_schemas.vfs.response import VFSResponse
from ...vssag_schemas.vfs.access_context import VSSAGAccessContext
from .bash import VFSBashRestorer
from .just_bash import JustBashParser
from .tool_use import ToolUseRestorer


class VFSRestoreService:
    """Two restore modes: structured Tool-Use and Bash/Grep interception."""

    def __init__(self, store, config):
        vfs_config = config.retrieval.vfs
        tool = ToolUseRestorer(
            VirtualFileSystemBuilder(
                store,
                vfs_config.root_prefix,
                vfs_config.mount_name,
            ),
            config,
        )
        self.tool_use = tool
        self.bash = VFSBashRestorer(
            tool,
            JustBashParser(
                timeout_seconds=vfs_config.max_query_seconds,
                worker_dir=vfs_config.just_bash_worker_dir,
            ),
        )

    async def restore_tool(self, request: VFSRequest) -> VFSResponse:
        return await self.tool_use.restore(request)

    def root(
        self,
        context: VSSAGAccessContext,
        mount_root: str | None = None,
    ) -> str:
        """返回模型可见的 VSSAG 根路径。"""

        return self.tool_use.builder.root(context, mount_root)

    async def build(
        self,
        context: VSSAGAccessContext,
        mount_root: str | None = None,
    ) -> dict[str, str]:
        """通过唯一 VFS 构建入口生成权限裁剪后的文件视图。"""

        return await self.tool_use.builder.build(
            context,
            mount_root,
        )

    async def build_tree(
        self,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
        path: str | None = None,
        max_depth: int | None = None,
    ) -> str:
        """通过唯一 VFS 构建入口生成受限目录树。"""

        return await self.tool_use.builder.build_tree(
            context,
            mount_root=mount_root,
            path=path,
            max_depth=max_depth,
        )

    async def restore_bash(
        self,
        command: str,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
    ) -> VFSResponse:
        """拦截受限 Bash 指令并返回终端风格文本。"""

        return await self.bash.execute(command, context, mount_root=mount_root)

    async def list_entries(
        self,
        path: str,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
    ) -> VFSResponse:
        return await self.restore_tool(
            VFSRequest(
                mode="dirs",
                path=path,
                context=context,
                mount_root=mount_root,
            )
        )

    async def read_file(
        self,
        path: str,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
    ) -> VFSResponse:
        return await self.restore_tool(
            VFSRequest(
                mode="full",
                path=path,
                context=context,
                mount_root=mount_root,
            )
        )

    async def read_range(
        self,
        path: str,
        line_range: str,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
    ) -> VFSResponse:
        return await self.restore_tool(
            VFSRequest(
                mode="range",
                path=path,
                value=line_range,
                context=context,
                mount_root=mount_root,
            )
        )

    async def grep(
        self,
        path: str,
        pattern: str | None,
        mode: str,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
    ) -> VFSResponse:
        return await self.restore_tool(
            VFSRequest(
                mode=mode,
                path=path,
                value=pattern,
                context=context,
                mount_root=mount_root,
            )
        )

    async def close(self) -> None:
        """释放 VSSAG VFS 使用的 Worker。"""

        await self.bash.close()
