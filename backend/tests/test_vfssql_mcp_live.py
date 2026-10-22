"""显式验证 VFSSQL 的真实 OAuth、工具节点及 Gateway 模型对话。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.mcp.oauth import OAuthTokenManager
from deerflow.mcp.tools import get_mcp_tools

pytestmark = pytest.mark.live

if os.environ.get("CI") or os.environ.get("DEER_FLOW_RUN_LIVE_TESTS") != "1":
    pytest.skip("显式设置 DEER_FLOW_RUN_LIVE_TESTS=1 才访问真实 VFSSQL/Keycloak", allow_module_level=True)


def _query() -> str:
    """读取必须命中真实索引的验收表名。

    Args:
        无；由操作人员设置 VFSSQL_E2E_QUERY。
    Returns:
        非空的真实表名。
    """
    query = os.environ.get("VFSSQL_E2E_QUERY", "").strip()
    assert query, "必须设置 VFSSQL_E2E_QUERY 为实际已索引表名"
    return query


def _result(message) -> dict:
    """提取并检查真实 ToolMessage，不把错误响应当作检索成功。

    Args:
        message: ToolNode 返回的工具消息。
    Returns:
        已检查成功状态的结构化工具结果。
    """
    assert message.status == "success", "真实 MCP 工具调用失败"
    payload = getattr(message.artifact, "structured_content", None)
    if payload is None:
        content = message.content
        if isinstance(content, list):
            content = next(item["text"] for item in content if item.get("type") == "text")
        payload = json.loads(content)
    assert isinstance(payload, dict) and payload.get("status") == "success", "MCP 返回业务失败结果"
    return payload


@pytest.mark.asyncio
async def test_vfssql_oauth_toolnode_retrieval_and_reacquisition():
    """通过宿主实际配置验证自动取令牌、八工具发现及过期后重新获取。

    Args:
        无；启动进程必须已加载实际凭据环境变量和扩展配置路径。
    Returns:
        无；全部断言通过才视为真实索引读取验收成功。
    """
    config_path = Path(os.environ.get("DEER_FLOW_EXTENSIONS_CONFIG_PATH", ""))
    assert config_path.is_absolute() and config_path.is_file(), "必须指定实际扩展配置的绝对路径"
    config = ExtensionsConfig.from_file(str(config_path))
    server = config.mcp_servers["vfssql"]
    assert server.enabled and server.type == "http" and server.tool_name_prefix
    assert server.oauth and server.oauth.client_secret and server.oauth.grant_type == "client_credentials"
    manager = OAuthTokenManager.from_extensions_config(config)
    # 仅记录真实请求次数，不替换 HTTP 端点或返回内容。
    manager._fetch_token = AsyncMock(wraps=manager._fetch_token)
    async with asyncio.timeout(120):
        assert (await manager.get_authorization_header("vfssql")).startswith("Bearer ")
        await manager.get_authorization_header("vfssql")
        assert manager._fetch_token.await_count == 1
        tools = [tool for tool in await get_mcp_tools() if tool.name.startswith("vfssql_")]
        assert {tool.name for tool in tools} == {"vfssql_" + name for name in ("vfs_tree_folder", "vfs_bash_execute", "vfs_grep_file", "vfs_cat_read", "vfs_find_file", "vfs_sed", "sql_execute", "sql_learn")}
        builder = StateGraph(MessagesState, context_schema=dict)
        builder.add_node("tools", ToolNode(tools, handle_tool_errors=True))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()

        async def call(name: str, arguments: dict) -> dict:
            """驱动真实图节点执行工具，保留 runtime 和 interceptor 链路。

            Args:
                name: VFSSQL 原始工具名。
                arguments: 不含身份或凭据的业务参数。
            Returns:
                已确认成功的工具结果。
            """
            result = await graph.ainvoke(
                {"messages": [AIMessage(content="", tool_calls=[{"name": "vfssql_" + name, "args": arguments, "id": name, "type": "tool_call"}])]},
                context={"thread_id": "vfssql-live-test", "user_id": "vfssql-live-test", "run_id": "retrieval"},
            )
            return _result(result["messages"][-1])

        listing = await call("vfs_tree_folder", {"show_summary": False})
        assert bool(listing.get("workspace_content")), "真实虚拟目录为空"
        root = listing["workspace_content"].splitlines()[0].strip().rstrip("/")
        search = await call("vfs_find_file", {"folder_path": root, "type": "folder", "patterns": [_query()], "max_results": 3})
        folders = [line.split("\t", 1)[1] for line in search["result_content"].splitlines() if line.startswith("folder\t")]
        assert folders, "实际索引未命中验收表目录"
        path = folders[0].rstrip("/") + "/desc.md"
        read = await call("vfs_cat_read", {"file_path": path})
        assert bool(read.get("result_content")), "未实际读取命中表的 desc.md"
        needle = next(line.strip() for line in read["result_content"].splitlines() if line.strip())
        grep = await call("vfs_grep_file", {"querys": [needle], "paths": [path], "top_k": 3})
        assert bool(grep.get("matches")), "grep 未命中刚读取的内容"
        excerpt = await call("vfs_sed", {"file_path": path, "operation": "print_range", "start_line": 1, "end_line": 3})
        assert bool(excerpt.get("result_content")), "sed 未读取原文范围"
        # 强制客户端缓存过期；仍向真实 Keycloak 请求，避免等待整个令牌 TTL。
        manager._tokens["vfssql"].expires_at = datetime(2000, 1, 1, tzinfo=UTC)
        assert (await manager.get_authorization_header("vfssql")).startswith("Bearer ")
        assert manager._fetch_token.await_count == 2


def test_vfssql_gateway_conversation():
    """通过登录、线程和实际 Agent 对话完成检索，不用预制工具消息替代模型。

    Args:
        无；额外设置 VFSSQL_E2E_GATEWAY_URL、EMAIL、PASSWORD、MODEL。
    Returns:
        无；真实工具成功、运行成功且回答引用读取路径才通过。
    """
    base = os.environ.get("VFSSQL_E2E_GATEWAY_URL")
    email = os.environ.get("VFSSQL_E2E_EMAIL")
    password = os.environ.get("VFSSQL_E2E_PASSWORD")
    model = os.environ.get("VFSSQL_E2E_MODEL")
    if not all((base, email, password, model)):
        pytest.skip("完整 Gateway 对话需显式设置 URL、EMAIL、PASSWORD、MODEL")
    query = _query()
    with httpx.Client(base_url=base, headers={"Origin": base}, timeout=240, trust_env=False) as client:
        login = client.post("/api/v1/auth/login/local", data={"username": email, "password": password, "remember_me": "false"})
        assert login.status_code == 200, "验收账号登录失败"
        csrf = client.cookies.get("csrf_token")
        assert csrf, "登录未设置 CSRF cookie"
        client.headers["X-CSRF-Token"] = csrf
        created = client.post("/api/threads", json={"metadata": {"title": "VFSSQL live 检索验收"}})
        assert created.status_code == 200, "验收线程创建失败"
        thread = created.json()["thread_id"]
        prompt = (
            f"请用 VFSSQL MCP 先调用 vfssql_vfs_tree_folder 查看目录，再调用 vfssql_vfs_find_file 找到名称为 {query} 的表目录，"
            "用 vfssql_vfs_cat_read 读取命中目录的 desc.md，并用 vfssql_vfs_grep_file 核对正文。"
            "根据文档说明字段与类型并引用完整虚拟路径；未记载的关系不能推断，不执行 SQL，不提交知识候选。"
        )
        response = client.post(
            f"/api/threads/{thread}/runs/wait",
            json={
                "assistant_id": "lead_agent",
                "input": {
                    "messages": [
                        {
                            "type": "human",
                            "content": prompt,
                        }
                    ]
                },
                "context": {"model_name": model, "thinking_enabled": False, "subagent_enabled": False},
                "config": {"recursion_limit": 256},
            },
        )
        assert response.status_code == 200, "真实 Gateway 对话请求失败"
        # wait 可能返回错误运行的部分 checkpoint；必须额外检查 run 状态。
        runs = client.get(f"/api/threads/{thread}/runs")
        assert runs.status_code == 200 and runs.json()[0]["status"] == "success", "真实 Agent 运行未成功完成"
        messages = response.json()["messages"]
        results = {message.get("name"): message for message in messages if message.get("type") == "tool" and message.get("name", "").startswith("vfssql_")}
        assert {"vfssql_vfs_tree_folder", "vfssql_vfs_find_file", "vfssql_vfs_cat_read", "vfssql_vfs_grep_file"}.issubset(results), "模型未完成检索和读取闭环"
        assert all(message.get("status") == "success" for message in results.values()), "模型调用 MCP 工具失败"
        assert not {"vfssql_sql_execute", "vfssql_sql_learn"}.intersection(results), "只读检索验收意外调用执行或学习工具"
        for message in results.values():
            content = message["content"]
            if isinstance(content, list):
                content = next(item["text"] for item in content if item.get("type") == "text")
            assert json.loads(content).get("status") == "success", "模型工具调用返回业务失败"
        # 按工具调用 ID 核对真实读取路径，避免把模型自行生成的路径当作读取证据。
        read_id = results["vfssql_vfs_cat_read"]["tool_call_id"]
        read_call = next(call for message in messages for call in message.get("tool_calls", []) if call["id"] == read_id)
        path = read_call["args"]["file_path"]
        answer = next(message for message in reversed(messages) if message.get("type") == "ai" and not message.get("tool_calls"))
        assert path.endswith("/desc.md") and path in str(answer.get("content")), "最终回答没有引用真实读取路径"
