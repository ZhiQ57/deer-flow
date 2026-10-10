"""显式验收独立服务的真实配置启动，不查询业务数据或调用外部模型。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.mcp.client import build_server_params

pytestmark = pytest.mark.live

if os.environ.get("CI") or os.environ.get("DEER_FLOW_RUN_LIVE_TESTS") != "1":
    pytest.skip("显式设置 DEER_FLOW_RUN_LIVE_TESTS=1 才验收真实 VSSAG 配置", allow_module_level=True)


@pytest.mark.asyncio
async def test_operator_config_starts_private_stdio_and_rejects_anonymous():
    """真实子进程初始化、工具发现与匿名拒绝，不以空目录冒充检索通过。

    Args:
        operator 通过 DEER_FLOW_EXTENSIONS_CONFIG_PATH 指定实际 Gateway 扩展配置。
    Returns:
        无；仅初始化和身份拒绝成功才通过。
    """
    config_path = Path(os.environ.get("DEER_FLOW_EXTENSIONS_CONFIG_PATH", ""))
    assert config_path.is_absolute() and config_path.is_file(), "必须指定实际 Gateway 扩展配置的绝对路径"
    server = ExtensionsConfig.from_file(str(config_path)).mcp_servers["vssag"]
    assert server.enabled and server.type == "stdio", "实际 Gateway 配置必须启用 VSSAG stdio 服务"
    configured = build_server_params("vssag", server)
    # 完全沿用实际配置，不在测试中额外补凭据，避免测试通过而网页启动失败。
    parameters = StdioServerParameters(
        command=configured["command"],
        args=configured["args"],
        env=configured.get("env"),
        cwd=configured.get("cwd"),
    )
    # 启动错误可能带配置值；保存到自动删除的临时文件，不回显到测试日志。
    with tempfile.TemporaryFile(mode="w+b") as errors:
        async with asyncio.timeout(45):
            async with stdio_client(parameters, errlog=errors) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    assert {item.name for item in tools} == {"build_vfs", "search", "grep_file"}
                    assert all(item.annotations.readOnlyHint for item in tools)
                    for name, arguments in (("build_vfs", {}), ("search", {"query": "anonymous probe"}), ("grep_file", {"mode": "dirs", "path": "/home/mnt/knowledge/vssag/"})):
                        assert (await session.call_tool(name, arguments)).isError
