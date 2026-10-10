"""只约束外置 MCP 封装的非阻塞边界，不替 SDK 后端作保证。"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "mcp-extensions/vssag"))
pytest.importorskip("vssag")

from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage
from mcp.types import CallToolResult, TextContent
from middlewares import vssag_directory as directory  # noqa: E402
from vssag import SearchRequest, VFSRequest, VSSAGAccessContext  # noqa: E402, F401
from vssag.vfs.paths import vssag_root  # noqa: E402, F401
from vssag_mcp_server.server import create_server  # noqa: E402

from deerflow.config.app_config import AppConfig
from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.config.paths import Paths


@pytest.fixture
def initialized_server(tmp_path):
    """准备同步初始化会读文件的客户端，供严格非阻塞检测使用。"""
    source = tmp_path / "config.txt"
    source.write_text("operator-config", encoding="utf-8")
    observed = {}

    class Client:
        """异步检索与关闭仍在当前服务事件循环执行。"""

        async def build_vfs(self, context, *, mount_root):
            observed["query_thread"] = threading.get_ident()
            return f"{mount_root}/vssag/"

        async def close(self):
            observed["closed"] = True

    def factory():
        """模拟 SDK 同步读取本地配置的初始化路径。"""
        observed["init_thread"] = threading.get_ident()
        observed["config"] = source.read_text(encoding="utf-8")
        return Client()

    return create_server(client_factory=factory), observed


@pytest.mark.asyncio
async def test_server_initialization_does_not_block_event_loop(initialized_server):
    """真实 MCP 生命周期在线程初始化 SDK，工具请求在事件循环反馈。"""
    server, observed = initialized_server
    event_thread = threading.get_ident()
    identity = {
        "user_id": "alice",
        "user_role": "analyst",
        "authz_attributes": {},
        "thread_id": "thread-1",
        "run_id": "run-1",
    }
    async with asyncio.timeout(10):
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "build_vfs",
                {},
                meta={"headers": {"X-DeerFlow-VSSAG-Context": json.dumps(identity)}},
            )
            assert not result.isError
    assert observed == {
        "init_thread": observed["init_thread"],
        "config": "operator-config",
        "query_thread": event_thread,
        "closed": True,
    }
    assert observed["init_thread"] != event_thread


@pytest.fixture
def initialized_directory_host(tmp_path, monkeypatch):
    """将会读文件的宿主配置与普通会话隔离到测试目录。"""
    config_file = tmp_path / "host.txt"
    config_file.write_text("configured", encoding="utf-8")
    config = AppConfig.model_construct()
    config.extensions = ExtensionsConfig.model_validate({"mcpServers": {"vssag": {"command": "test-python"}}})
    observed = {}

    def load_config():
        observed["config_thread"] = threading.get_ident()
        assert config_file.read_text(encoding="utf-8") == "configured"
        return config

    session = SimpleNamespace(call_tool=AsyncMock(return_value=CallToolResult(content=[TextContent(type="text", text="/home/mnt/knowledge/vssag/tree.md")])))
    pool = SimpleNamespace(get_session=AsyncMock(return_value=session))
    monkeypatch.setattr(directory, "get_app_config", load_config)
    monkeypatch.setattr(directory.ExtensionsConfig, "from_file", lambda *args, **kwargs: config.extensions)
    monkeypatch.setattr(directory, "get_paths", lambda: Paths(tmp_path / "runtime"))
    monkeypatch.setattr(directory, "get_session_pool", lambda: pool)
    return observed


@pytest.mark.asyncio
async def test_host_directory_configuration_and_workspace_io_are_off_loop(initialized_directory_host):
    """首轮注入实际经过配置读取与目录准备，但不得阻塞事件循环。"""
    identity = {"user_id": "alice", "user_role": "user", "thread_id": "thread-1", "run_id": "run-1", "authz_attributes": {}}
    request = ModelRequest(model=SimpleNamespace(), messages=[HumanMessage(content="查知识库")], tools=[SimpleNamespace(name="vssag_build_vfs")], runtime=SimpleNamespace(context=identity))
    handler = AsyncMock()
    await directory.VssagDirectoryMiddleware().awrap_model_call(request, handler)
    assert "tree.md" in str(handler.call_args.args[0].messages)
    assert initialized_directory_host["config_thread"] != threading.get_ident()
