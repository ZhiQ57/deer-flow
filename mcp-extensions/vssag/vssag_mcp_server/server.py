"""VSSAG 私有 stdio MCP 服务；不提供 HTTP、Bash 或真实文件写入。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .interceptor import CONTEXT_HEADER, MAX_CONTEXT_BYTES

logger = logging.getLogger(__name__)


def access_context(ctx: Context):
    """读取私有 stdio 调用中由宿主附加的身份，不信任工具参数。

    Args:
        ctx: MCP 请求上下文；stdio 的父进程是本服务的信任边界。
    Returns:
        SDK 定义的访问上下文；缺失或非法元数据时直接拒绝。
    """
    from vssag import VSSAGAccessContext

    meta = ctx.request_context.meta
    headers = getattr(meta, "headers", None)
    if not isinstance(headers, dict):
        raise ValueError("VSSAG requires trusted stdio context metadata")
    matches = [value for name, value in headers.items() if name.lower() == CONTEXT_HEADER.lower()]
    if len(matches) != 1 or not isinstance(matches[0], str) or len(matches[0].encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ValueError("Invalid VSSAG context metadata")
    try:
        values = json.loads(matches[0])
        if not isinstance(values, dict):
            raise ValueError("invalid context")
        return VSSAGAccessContext.model_validate(values)
    except (ValueError, TypeError):
        raise ValueError("Invalid VSSAG context metadata") from None


def create_server(
    *,
    mount_root: str = "/home/mnt/knowledge",
    timeout_seconds: float = 30,
    client_factory: Callable[[], Any] | None = None,
) -> FastMCP:
    """创建独立 MCP 服务，整个生命周期由一个事件循环管理。

    Args:
        mount_root: 交给 SDK 拼接虚拟子目录的逻辑根路径。
        timeout_seconds: 单次 SDK 查询的有限正数超时。
        client_factory: 测试注入入口；生产默认通过 SDK from_env 初始化。
    Returns:
        只公开目录、搜索和文档读取三个 MCP 工具的服务。
    """
    from vssag.vfs.paths import vssag_root

    vssag_root(mount_root)
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")

    @asynccontextmanager
    async def lifespan(server):
        """在服务启动和关闭时管理 SDK，不建立跨循环共享客户端。

        Args:
            server: 当前 MCP 服务对象。
        Returns:
            包含 SDK 客户端和并发锁的服务上下文。
        """
        del server
        if client_factory is None:
            from vssag import VSSAGClient

            client = await asyncio.to_thread(VSSAGClient.from_env)
        else:
            client = await asyncio.to_thread(client_factory)
        try:
            yield {"client": client, "lock": asyncio.Lock()}
        finally:
            await client.close()

    server = FastMCP(
        "vssag",
        instructions="Use build_vfs, search and grep_file for knowledge retrieval. Virtual paths are not sandbox files. Use DeerFlow's normal file tools separately to save retrieved results. Never use bash to read these virtual paths.",
        lifespan=lifespan,
    )

    async def invoke(ctx, operation, **arguments):
        """每次重建权限上下文并串行调用 SDK，避免单连接并发冲突。

        Args:
            ctx: 当前 MCP 请求。
            operation: 固定的 SDK 只读操作名。
            arguments: 已通过 MCP Schema 校验的检索参数。
        Returns:
            SDK 的目录文本或 JSON 可序列化检索结果。
        """
        from vssag import SearchRequest, VFSRequest

        identity = access_context(ctx)
        resources = ctx.request_context.lifespan_context
        try:
            async with asyncio.timeout(timeout_seconds):
                async with resources["lock"]:
                    client = resources["client"]
                    if operation == "build_vfs":
                        return await client.build_vfs(identity, mount_root=mount_root)
                    if operation == "search":
                        response = await client.search(SearchRequest(context=identity, **arguments))
                    else:
                        response = await client.restore_tool(VFSRequest(context=identity, mount_root=mount_root, **arguments))
                    if getattr(response, "ok", True) is False:
                        raise ValueError("VSSAG rejected the virtual file request")
                    return response.model_dump(mode="json")
        except TimeoutError:
            raise ValueError("VSSAG retrieval timed out; no sandbox fallback was executed") from None
        except Exception:
            logger.warning("VSSAG %s failed", operation)
            raise ValueError("VSSAG retrieval failed; check the service configuration and access permissions") from None

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def build_vfs(ctx: Context) -> str:
        """列出当前用户有权访问的知识库目录，不返回文件正文。

        返回路径仅用于 VSSAG MCP 工具，不是 Bash 可访问的 Sandbox 文件。

        Args:
            ctx: MCP 注入的可信请求上下文，模型无需提供。
        Returns:
            带完整虚拟根路径的 tree 文本。
        """
        return await invoke(ctx, "build_vfs")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def search(
        ctx: Context,
        query: str,
        top_k: Annotated[int, Field(ge=1, le=200)] = 10,
        strategy: Literal["keyword", "vector", "hybrid", "graph"] = "hybrid",
    ) -> dict:
        """检索当前用户有权访问的文档，保留出处用于后续读取和引用。

        如需保存检索结果，随后独立调用 DeerFlow 的 write_file；不要拼接 Bash 指令。

        Args:
            ctx: MCP 注入的可信请求上下文。
            query: 用户的检索关键词或问题。
            top_k: 返回条数，SDK 限制为 1 到 200。
            strategy: 检索策略，必须有相应后端配置。
        Returns:
            包含命中内容和出处的 SDK 检索结果。
        """
        return await invoke(ctx, "search", query=query, top_k=top_k, strategy=strategy)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def grep_file(
        ctx: Context,
        mode: Literal["dirs", "full", "range", "lines", "heading", "block"],
        path: str,
        value: str | None = None,
    ) -> dict:
        """按知识库虚拟路径列目录、读正文或查找行，不访问真实 Sandbox。

        Args:
            ctx: MCP 注入的可信请求上下文。
            mode: 目录、全文、行范围、匹配行、标题或内容块模式。
            path: build_vfs 返回的公开虚拟路径。
            value: 范围或匹配条件，例如 range 模式的 1-20。
        Returns:
            SDK 返回的结构化文档内容及来源。
        """
        return await invoke(ctx, "grep_file", mode=mode, path=path, value=value)

    return server


def main() -> None:
    """以私有 stdio 启动服务，拒绝依赖调用进程的当前工作目录。

    Args:
        CLI 参数为 --config、--root 和 --timeout。
    Returns:
        服务退出后返回；配置错误直接终止启动。
    """
    parser = argparse.ArgumentParser(description="Read-only VSSAG MCP server (trusted stdio only)")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", default="/home/mnt/knowledge")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not args.config.is_absolute() or not args.config.is_file():
        parser.error("--config must name an existing absolute VSSAG YAML path")
    config_path = args.config.resolve()
    os.environ["CONFIG_FILE"] = str(config_path)
    # SDK 会读取 cwd 的 dotenv；禁止模型可写工作区影响服务端数据源和凭据。
    os.chdir(config_path.parent)
    logging.basicConfig(level=logging.WARNING)
    create_server(mount_root=args.root, timeout_seconds=args.timeout).run(transport="stdio")


if __name__ == "__main__":
    main()
