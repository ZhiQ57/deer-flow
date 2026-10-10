"""验证外置 VSSAG 的标准 MCP 协议及可信上下文，不连接生产数据源。"""

from __future__ import annotations

import asyncio
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.shared.memory import create_connected_server_and_client_session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp-extensions/vssag"))

from vssag_mcp_server.interceptor import build_context_interceptor  # noqa: E402
from vssag_mcp_server.server import create_server  # noqa: E402

pytest.importorskip(
    "vssag",
    reason="可选集成测试需要安装 mcp-extensions/vssag[server] 及其 extension SDK",
)


class RecordingClient:
    """记录测试调用，不替代生产环境的 SDK 实现。"""

    def __init__(self):
        self.calls = []
        self.closed = False

    async def build_vfs(self, context, *, mount_root):
        self.calls.append(("tree", context, mount_root))
        await asyncio.sleep(0)
        return f"{mount_root}/vssag/\n└── {context.user_id}-report.md"

    async def search(self, request):
        self.calls.append(("search", request.context, request.query))
        return SimpleNamespace(
            model_dump=lambda **kwargs: {
                "hits": [{"content": request.query}],
                "total": 1,
            }
        )

    async def restore_tool(self, request):
        self.calls.append(("read", request.context, request.path))
        return SimpleNamespace(model_dump=lambda **kwargs: {"ok": True, "content": "订单检索结果\n"})

    async def close(self):
        self.closed = True


def runtime(user_id="alice", **attributes):
    """构造由宿主认证后提供的 runtime 测试上下文。"""
    return SimpleNamespace(
        context={
            "user_id": user_id,
            "user_role": "analyst",
            "authz_attributes": attributes,
            "thread_id": "thread-1",
            "run_id": "run-1",
        }
    )


async def call_tool(session, name, arguments, user_runtime):
    """通过真实 MCP 请求传递由外置拦截器生成的元数据。"""
    request = MCPToolCallRequest(name=name, args=arguments, server_name="vssag", runtime=user_runtime)

    async def handler(actual):
        return await session.call_tool(actual.name, actual.args, meta={"headers": actual.headers})

    return await build_context_interceptor()(request, handler)


@pytest.mark.asyncio
async def test_mcp_discovery_multi_turn_and_parallel_context():
    """真实 tools/list、tools/call 支持多轮及并行身份隔离。"""
    client = RecordingClient()
    server = create_server(client_factory=lambda: client)
    async with create_connected_server_and_client_session(server) as session:
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == {"build_vfs", "search", "grep_file"}
        for tool in tools:
            assert not {
                "user_id",
                "user_role",
                "authz_attributes",
                "thread_id",
                "run_id",
                "mount_root",
            }.intersection(tool.inputSchema["properties"])
            assert tool.annotations.readOnlyHint is True
        search_schema = next(tool.inputSchema for tool in tools if tool.name == "search")
        assert search_schema["properties"]["top_k"]["maximum"] == 200
        trees = await asyncio.gather(*(call_tool(session, "build_vfs", {}, runtime(user)) for user in ("alice", "bob")))
        assert all(not result.isError for result in trees)
        assert "alice-report" in trees[0].content[0].text
        assert "bob-report" not in trees[0].content[0].text
        searched = await call_tool(session, "search", {"query": "订单", "strategy": "keyword"}, runtime())
        assert not searched.isError
        read = await call_tool(
            session,
            "grep_file",
            {"mode": "full", "path": "/home/mnt/knowledge/vssag/alice-report.md"},
            runtime(),
        )
        assert not read.isError
        assert [entry[0] for entry in client.calls] == [
            "tree",
            "tree",
            "search",
            "read",
        ]
    assert client.closed


@pytest.mark.asyncio
async def test_server_rejects_missing_context_before_sdk_call():
    """未携带宿主上下文时拒绝检索，不降级为匿名或管理员。"""
    client = RecordingClient()
    async with create_connected_server_and_client_session(create_server(client_factory=lambda: client)) as session:
        result = await session.call_tool("build_vfs", {})
        assert result.isError
        assert client.calls == []


@pytest.mark.asyncio
async def test_interceptor_does_not_touch_other_servers_or_arguments():
    """仅向目标 MCP 注入元数据，不注册或拦截 Bash。"""
    interceptor = build_context_interceptor()
    request = MCPToolCallRequest(
        name="bash",
        args={"command": "echo unchanged"},
        server_name="other",
        runtime=None,
    )
    seen = []

    async def handler(actual):
        seen.append(actual)
        return "unchanged"

    assert await interceptor(request, handler) == "unchanged"
    assert seen == [request]
    request = MCPToolCallRequest(
        name="search",
        args={"query": "订单"},
        server_name="vssag",
        runtime=runtime(workspace_ids=["team"]),
    )
    await interceptor(request, handler)
    assert seen[-1].args == request.args
    assert request.headers is None
    forwarded = json.loads(seen[-1].headers["X-DeerFlow-VSSAG-Context"])
    assert forwarded["authz_attributes"] == {"workspace_ids": ["team"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context",
    [
        {},
        {"user_id": "alice"},
        {"user_id": "alice", "user_role": "analyst", "thread_id": "thread-1"},
    ],
)
async def test_interceptor_requires_complete_trusted_identity(context):
    """缺少可信身份或执行坐标时，拦截器不会发出请求。"""
    request = MCPToolCallRequest(
        name="build_vfs",
        args={},
        server_name="vssag",
        runtime=SimpleNamespace(context=context),
    )

    async def handler(actual):
        pytest.fail("不应发送身份不完整的请求")

    with pytest.raises(ValueError, match="context"):
        await build_context_interceptor()(request, handler)


@pytest.mark.asyncio
async def test_model_cannot_supply_authorization_arguments():
    """模型工具参数不能覆盖运行时权限。"""
    request = MCPToolCallRequest(
        name="search",
        args={"query": "订单", "user_id": "admin"},
        server_name="vssag",
        runtime=runtime(),
    )

    async def handler(actual):
        pytest.fail("不应接受模型生成的授权字段")

    with pytest.raises(ValueError, match="identity"):
        await build_context_interceptor()(request, handler)


def test_example_keeps_default_bash_with_external_directory_middleware():
    """外置目录中间件由既有配置加载，不改变 Harness Bash。"""
    example = json.loads((ROOT / "mcp-extensions/vssag/extensions_config.example.json").read_text(encoding="utf-8"))
    assert example["middlewares"] == ["middlewares.vssag_directory:VssagDirectoryMiddleware"]
    assert example["mcpServers"]["vssag"]["enabled"] is False
    assert example["mcpServers"]["vssag"]["type"] == "stdio"
    assert example["mcpServers"]["vssag"]["args"][:3] == [
        "-I",
        "-m",
        "vssag_mcp_server.server",
    ]
    assert example["mcpInterceptors"] == ["vssag_mcp_server.interceptor:build_context_interceptor"]
    source = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    assert "use: deerflow.sandbox.tools:bash_tool" in source
    assert "use: deerflow.knowledge:install" not in source


def test_migrated_server_packaging_keeps_sdk_out_of_gateway_dependencies():
    """服务使用同目录 SDK；Gateway 安装基础包不会拉入服务端依赖。"""
    project_root = ROOT / "mcp-extensions/vssag"
    project = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"].get("dependencies", []) == []
    assert "ownmindforge-vssag>=0.1.0.dev0" in project["project"]["optional-dependencies"]["server"]
    assert project["project"]["scripts"]["deerflow-vssag-mcp"] == "vssag_mcp_server.server:main"
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "vssag_mcp_server",
        "middlewares",
    ]
    sdk_source = project["tool"]["uv"]["sources"]["ownmindforge-vssag"]
    assert sdk_source == {"path": "extension", "editable": True}
    assert (project_root / sdk_source["path"] / "vssag/client.py").is_file()


@pytest.mark.asyncio
async def test_sdk_failures_and_timeout_are_errors_without_secret_details():
    """检索错误与超时返回 MCP 错误，不泄露底层配置细节。"""

    class FailingClient(RecordingClient):
        async def build_vfs(self, context, *, mount_root):
            raise RuntimeError("postgres://private-password@private-host")

    async with create_connected_server_and_client_session(create_server(client_factory=FailingClient)) as session:
        result = await call_tool(session, "build_vfs", {}, runtime())
        assert result.isError
        assert "private-password" not in result.content[0].text

    class SlowClient(RecordingClient):
        async def build_vfs(self, context, *, mount_root):
            await asyncio.sleep(5)

    async with create_connected_server_and_client_session(create_server(client_factory=SlowClient, timeout_seconds=0.01)) as session:
        result = await call_tool(session, "build_vfs", {}, runtime())
        assert result.isError
        assert "timed out" in result.content[0].text


@pytest.mark.asyncio
async def test_server_validates_metadata_and_model_identity_override():
    """服务端独立拒绝非法元数据及模型添加的身份字段。"""
    client = RecordingClient()
    async with create_connected_server_and_client_session(create_server(client_factory=lambda: client)) as session:
        for meta in (
            {},
            {"headers": {}},
            {"headers": {"X-DeerFlow-VSSAG-Context": "{}"}},
        ):
            assert (await session.call_tool("build_vfs", {}, meta=meta)).isError
        assert not client.calls
        meta = {"headers": {"X-DeerFlow-VSSAG-Context": json.dumps(runtime().context)}}
        result = await session.call_tool("build_vfs", {"user_id": "admin"}, meta=meta)
        if not result.isError:
            assert client.calls[-1][1].user_id == "alice"


@pytest.mark.asyncio
async def test_real_sdk_permissions_and_normal_tool_feedback(tmp_path, monkeypatch):
    """真实 SDK、MCP 协议和 DeerFlow pooled 工具封装完成目录到读取反馈。"""
    from unittest.mock import AsyncMock

    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_mcp_adapters.tools import load_mcp_tools
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode
    from middlewares import vssag_directory as directory
    from middlewares.vssag_directory import VssagDirectoryMiddleware
    from vssag import IndexPayload, VSSAGClient
    from vssag.indexing.index_stores.memory_store import MemoryIndexStore

    from deerflow.agents.factory import create_deerflow_agent
    from deerflow.agents.lead_agent import prompt as lead_prompt
    from deerflow.config.app_config import AppConfig
    from deerflow.config.extensions_config import ExtensionsConfig
    from deerflow.config.paths import Paths
    from deerflow.mcp import tools as mcp_tools
    from deerflow.sandbox import tools as sandbox_tools
    from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping

    config_path = tmp_path / "vssag.yaml"
    config_path.write_text(
        "vssag:\n  parsing: {enabled: false}\n  index:\n"
        "    embedding: {enabled: false, engine: memory}\n    graph: {enabled: false}\n    sql_ability: {enabled: false}\n"
        "  retrieval:\n    embedding_retriever: {enabled: false}\n    graph_retriever: {enabled: false}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(config_path))
    monkeypatch.setenv("APP_ENV", "test")
    for name in ("ENV_FILE", "OPENAI_API_KEY", "VSSAG_OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    store = MemoryIndexStore()
    for user_id in ("alice", "bob"):
        await store.upsert(
            IndexPayload(
                record_id=f"{user_id}-block",
                source_id=f"{user_id}-report",
                text=f"Invoice {user_id.upper()}_ONLY",
                metadata={"user_id": user_id},
            )
        )
    client = VSSAGClient.from_env(store=store)
    async with create_connected_server_and_client_session(create_server(client_factory=lambda: client)) as session:
        trees = await asyncio.gather(*(call_tool(session, "build_vfs", {}, runtime(user)) for user in ("alice", "bob")))
        assert "alice-report" in trees[0].content[0].text and "bob-report" not in trees[0].content[0].text
        assert "bob-report" in trees[1].content[0].text and "alice-report" not in trees[1].content[0].text
        denied = await call_tool(
            session,
            "grep_file",
            {"mode": "full", "path": "/home/mnt/knowledge/vssag/own/bob-report/md.md"},
            runtime("alice"),
        )
        assert denied.isError

        class SessionPool:
            """仅替换会话获取，实际 MCP 请求、工具转换与 runtime 注入保持真实。"""

            async def get_session(self, server_name, scope_key, connection):
                assert scope_key in {"alice:thread-1", "bob:thread-1"}
                return session

        monkeypatch.setattr(mcp_tools, "get_session_pool", SessionPool)
        monkeypatch.setattr(directory, "get_session_pool", SessionPool)
        app_config = AppConfig.model_construct()
        from deerflow.config.sandbox_config import SandboxConfig

        app_config.sandbox = SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider")
        app_config.extensions = ExtensionsConfig.model_validate({"mcpServers": {"vssag": {"type": "stdio", "command": sys.executable}}})
        monkeypatch.setattr(directory, "get_app_config", lambda: app_config)
        monkeypatch.setattr(directory.ExtensionsConfig, "from_file", lambda *args, **kwargs: app_config.extensions)
        monkeypatch.setattr(directory, "get_paths", lambda: Paths(tmp_path / "directory-runtime"))
        monkeypatch.setattr(lead_prompt, "get_skills_prompt_section", lambda *args, **kwargs: "")
        monkeypatch.setattr(lead_prompt, "get_agent_soul", lambda *args, **kwargs: "")
        system_prompt = lead_prompt.apply_prompt_template(app_config=app_config)
        discovered = await load_mcp_tools(session, server_name="vssag", tool_name_prefix=True)
        tools = [mcp_tools._make_session_pool_tool(tool, "vssag", {"transport": "http"}, [build_context_interceptor()]) for tool in discovered]
        graph_builder = StateGraph(MessagesState, context_schema=dict)
        graph_builder.add_node("tools", ToolNode(tools))
        graph_builder.add_edge(START, "tools")
        graph_builder.add_edge("tools", END)
        graph = graph_builder.compile()
        result = await graph.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "search-1",
                                "name": "vssag_search",
                                "args": {"query": "Invoice", "strategy": "keyword"},
                            },
                            {
                                "id": "read-1",
                                "name": "vssag_grep_file",
                                "args": {
                                    "mode": "full",
                                    "path": "/home/mnt/knowledge/vssag/own/alice-report/md.md",
                                },
                            },
                        ],
                    )
                ]
            },
            context=runtime("alice").context,
        )
        messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
        assert {message.tool_call_id for message in messages} == {"search-1", "read-1"}
        assert all(message.status == "success" for message in messages)
        for message in messages:
            serialized = json.dumps(message.content, ensure_ascii=False)
            assert "ALICE_ONLY" in serialized
            assert "BOB_ONLY" not in serialized

        workspace = tmp_path / "workspace"
        sandbox = LocalSandbox("test-vssag", [PathMapping("/mnt/user-data/workspace", str(workspace))])
        monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime: sandbox)
        monkeypatch.setattr(
            sandbox_tools,
            "ensure_sandbox_initialized_async",
            AsyncMock(return_value=sandbox),
        )
        monkeypatch.setattr(sandbox_tools, "ensure_thread_directories_exist", lambda runtime: None)
        monkeypatch.setattr(sandbox_tools, "safe_app_config_async", AsyncMock(return_value=None))
        monkeypatch.setattr(sandbox_tools, "authorize_sandbox_execution_async", AsyncMock())

        class RetrievalWorkflowModel(BaseChatModel):
            """以确定性模型驱动检索反馈、原生写文件和完成反馈。"""

            @property
            def _llm_type(self):
                return "vssag-retrieval-workflow-test"

            def bind_tools(self, tools, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                responses = [message for message in messages if isinstance(message, ToolMessage)]
                context = [message for message in messages if message.additional_kwargs.get("deerflow_vssag_directory")]
                assert len(context) == 1 and "alice-report" in context[0].content and "bob-report" not in context[0].content
                assert any("<working_directory" in str(message.content) and "/mnt/user-data/workspace" in str(message.content) for message in messages)
                assert not any(response.name == "vssag_build_vfs" for response in responses)
                stages = [
                    ("vssag_search", {"query": "Invoice", "strategy": "keyword"}),
                    (
                        "vssag_grep_file",
                        {
                            "mode": "full",
                            "path": context[0].content.split("--- BEGIN USER INPUT ---\n", 1)[1].splitlines()[0].rstrip("/") + "/own/alice-report/md.md",
                        },
                    ),
                ]
                if len(responses) < len(stages):
                    name, arguments = stages[len(responses)]
                elif len(responses) == len(stages):
                    # 将实际 MCP 正文交给独立的原生写文件调用，而非重放 Shell。
                    content = "\n".join(block["text"] for block in responses[-1].content if block.get("type") == "text")
                    assert "ALICE_ONLY" in content and "BOB_ONLY" not in content
                    name, arguments = (
                        "write_file",
                        {
                            "path": "/mnt/user-data/workspace/result.txt",
                            "content": content,
                        },
                    )
                else:
                    assert responses[-1].content == "OK"
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content="检索结果已保存"))])
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": f"workflow-{len(responses)}",
                            "name": name,
                            "args": arguments,
                        }
                    ],
                )
                return ChatResult(generations=[ChatGeneration(message=message)])

        agent = create_deerflow_agent(
            RetrievalWorkflowModel(),
            system_prompt=system_prompt,
            tools=[*tools, sandbox_tools.write_file_tool],
            middleware=[VssagDirectoryMiddleware()],
        )
        completed = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "检索我的订单并保存结果"}]},
            context=runtime().context,
        )
        assert completed["messages"][-1].content == "检索结果已保存"
        feedback = [message for message in completed["messages"] if isinstance(message, ToolMessage)]
        assert [message.name for message in feedback] == [
            "vssag_search",
            "vssag_grep_file",
            "write_file",
        ]
        assert [message.tool_call_id for message in feedback] == [f"workflow-{index}" for index in range(3)]
        assert not any(message.additional_kwargs.get("deerflow_vssag_directory") for message in completed["messages"])
        saved = (workspace / "result.txt").read_text(encoding="utf-8")
        assert "ALICE_ONLY" in saved and "BOB_ONLY" not in saved

        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import InMemorySaver

        class ConcurrentModel(BaseChatModel):
            """同一实例依据本轮实际检索结果回答，不缓存用户状态。"""

            @property
            def _llm_type(self):
                return "vssag-concurrent-workflow-test"

            def bind_tools(self, tools, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                start = max(index for index, message in enumerate(messages) if isinstance(message, HumanMessage))
                feedback = [message for message in messages[start + 1 :] if isinstance(message, ToolMessage)]
                context = [message for message in messages if message.additional_kwargs.get("deerflow_vssag_directory")]
                assert len(context) == 1 and "-report" in context[0].content
                if not feedback:
                    answer = AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": f"search-{start}",
                                "name": "vssag_search",
                                "args": {"query": "Invoice", "strategy": "keyword"},
                            }
                        ],
                    )
                else:
                    answer = AIMessage(content=json.dumps(feedback[-1].content, ensure_ascii=False))
                return ChatResult(generations=[ChatGeneration(message=answer)])

        shared_agent = create_deerflow_agent(
            ConcurrentModel(),
            tools=tools,
            middleware=[VssagDirectoryMiddleware()],
            checkpointer=InMemorySaver(),
        )

        async def ask(user, turn):
            """隔离检查点并传递新 run_id，验证多轮与并行互不串线。

            Args:
                user: 已知测试数据的 owner。
                turn: 当前测试轮次。
            Returns:
                真实检索工具反馈生成的最终消息。
            """
            context = {**runtime(user).context, "run_id": f"run-{turn}"}
            result = await shared_agent.ainvoke(
                {"messages": [HumanMessage(content="查询 Invoice")]},
                config={"configurable": {"thread_id": f"test-{user}"}},
                context=context,
            )
            assert not any(message.additional_kwargs.get("deerflow_vssag_directory") for message in result["messages"])
            assert not any(isinstance(message, ToolMessage) and message.name == "vssag_build_vfs" for message in result["messages"])
            return result["messages"][-1].content

        for turn in (1, 2):
            answers = await asyncio.gather(ask("alice", turn), ask("bob", turn))
            assert "ALICE_ONLY" in answers[0] and "BOB_ONLY" not in answers[0]
            assert "BOB_ONLY" in answers[1] and "ALICE_ONLY" not in answers[1]


@pytest.mark.asyncio
async def test_stdio_entrypoint_discovers_tools_in_a_child_process(tmp_path):
    """通过真实 stdio 子进程启动 CLI，验证发现与拒绝匿名调用。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    trusted_config = tmp_path / "operator"
    trusted_config.mkdir()
    config = trusted_config / "sdk.yaml"
    config.write_text(
        "vssag:\n  parsing: {enabled: false}\n  index:\n"
        "    embedding: {enabled: false, engine: memory}\n    graph: {enabled: false}\n    sql_ability: {enabled: false}\n"
        "  retrieval:\n    embedding_retriever: {enabled: false}\n    graph_retriever: {enabled: false}\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "model-workspace"
    workspace.mkdir()
    (workspace / ".env").mkdir()
    (workspace / "vssag_mcp_server.py").write_text("raise RuntimeError('untrusted workspace module')", encoding="utf-8")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "vssag_mcp_server.server", "--config", str(config)],
        env={"APP_ENV": "test", "PYTHONUTF8": "1"},
        cwd=str(workspace),
    )
    async with asyncio.timeout(45):
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 3
                assert (await session.call_tool("build_vfs", {})).isError
                result = await call_tool(session, "build_vfs", {}, runtime("alice"))
                assert not result.isError
                assert "/home/mnt/knowledge/vssag/" in result.content[0].text


@pytest.mark.asyncio
async def test_cancelled_waiter_is_bounded_by_server_timeout():
    """客户端取消未自动转发时，服务端超时释放锁，后续请求不串用户。"""
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class CancellableClient(RecordingClient):
        async def build_vfs(self, context, *, mount_root):
            if context.user_id == "alice":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return await super().build_vfs(context, mount_root=mount_root)

    client = CancellableClient()
    async with create_connected_server_and_client_session(create_server(client_factory=lambda: client, timeout_seconds=0.2)) as session:
        pending = asyncio.create_task(call_tool(session, "build_vfs", {}, runtime("alice")))
        await asyncio.wait_for(entered.wait(), 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await asyncio.wait_for(cancelled.wait(), 5)
        result = await asyncio.wait_for(call_tool(session, "build_vfs", {}, runtime("bob")), 5)
        assert not result.isError
        assert "bob-report" in result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attributes",
    [
        {"authz_attributes": []},
        {"authz_attributes": {"value": "a" * 17000}},
        {"authz_attributes": {"value": float("nan")}},
    ],
)
async def test_invalid_context_never_reaches_mcp(attributes):
    """授权属性必须可序列化、类型正确且不超过元数据上限。"""
    context = {**runtime().context, **attributes}
    request = MCPToolCallRequest(
        name="search",
        args={"query": "invoice"},
        server_name="vssag",
        runtime=SimpleNamespace(context=context),
    )

    async def handler(actual):
        pytest.fail("非法身份数据不应进入 MCP 传输")

    with pytest.raises(ValueError):
        await build_context_interceptor()(request, handler)


@pytest.mark.asyncio
async def test_interceptor_overwrites_case_insensitive_context_header():
    """配置中的旧身份头不能覆盖本次宿主认证身份。"""
    request = MCPToolCallRequest(
        name="build_vfs",
        args={},
        server_name="vssag",
        runtime=runtime(),
        headers={"x-deerflow-vssag-context": "old", "Trace": "retained"},
    )

    async def handler(actual):
        assert set(actual.headers) == {"X-DeerFlow-VSSAG-Context", "Trace"}
        assert json.loads(actual.headers["X-DeerFlow-VSSAG-Context"])["user_id"] == "alice"

    await build_context_interceptor()(request, handler)
    assert request.headers["x-deerflow-vssag-context"] == "old"


@pytest.mark.asyncio
@pytest.mark.parametrize("use_root_example", [False, True], ids=["standalone-example", "root-example"])
async def test_example_config_drives_real_deerflow_stdio_pool(tmp_path, monkeypatch, use_root_example):
    """从两份接入示例发现工具，经真实池化 stdio 反馈给 ToolNode。

    Args:
        tmp_path: 隔离的 SDK 和宿主配置目录。
        monkeypatch: 限定测试进程的环境与池作用域。
        use_root_example: 是否按指南合并根示例与外置入口。
    Returns:
        无；真实子进程、目录注入或多轮工具反馈失败时断言失败。
    """
    from unittest.mock import AsyncMock

    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode
    from middlewares import vssag_directory as directory
    from middlewares.vssag_directory import VssagDirectoryMiddleware

    from deerflow.config.app_config import AppConfig
    from deerflow.config.extensions_config import ExtensionsConfig
    from deerflow.config.paths import Paths
    from deerflow.mcp import tools as mcp_tools
    from deerflow.mcp.session_pool import MCPSessionPool

    sdk_config = tmp_path / "sdk.yaml"
    sdk_config.write_text(
        "vssag:\n  parsing: {enabled: false}\n  index:\n"
        "    embedding: {enabled: false, engine: memory}\n    graph: {enabled: false}\n    sql_ability: {enabled: false}\n"
        "  retrieval:\n    embedding_retriever: {enabled: false}\n    graph_retriever: {enabled: false}\n",
        encoding="utf-8",
    )
    example = json.loads((ROOT / "mcp-extensions/vssag/extensions_config.example.json").read_text(encoding="utf-8"))
    if use_root_example:
        root_example = json.loads((ROOT / "extensions_config.example.json").read_text(encoding="utf-8"))
        root_example["middlewares"] = example["middlewares"]
        root_example["mcpInterceptors"] = example["mcpInterceptors"]
        example = root_example
    example["mcpServers"]["vssag"]["enabled"] = True
    example["mcpServers"]["vssag"]["env"]["APP_ENV"] = "test"
    extensions = tmp_path / "extensions.json"
    extensions.write_text(json.dumps(example), encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions))
    monkeypatch.setenv("VSSAG_MCP_PYTHON", sys.executable)
    monkeypatch.setenv("VSSAG_MCP_CONFIG", str(sdk_config))
    paths = Paths(tmp_path / "runtime")
    pool = MCPSessionPool()
    monkeypatch.setattr(mcp_tools, "get_paths", lambda: paths)
    monkeypatch.setattr(mcp_tools, "get_session_pool", lambda: pool)
    monkeypatch.setattr(directory, "get_session_pool", lambda: pool)
    monkeypatch.setattr(directory, "get_paths", lambda: paths)
    app_config = AppConfig.model_construct()
    app_config.extensions = ExtensionsConfig.from_file(str(extensions))
    monkeypatch.setattr(directory, "get_app_config", lambda: app_config)
    try:
        async with asyncio.timeout(60):
            tools = await mcp_tools.get_mcp_tools()
            assert {tool.name for tool in tools} == {
                "vssag_build_vfs",
                "vssag_search",
                "vssag_grep_file",
            }
            builder = StateGraph(MessagesState, context_schema=dict)
            builder.add_node("tools", ToolNode(tools))
            builder.add_edge(START, "tools")
            builder.add_edge("tools", END)
            graph = builder.compile()
            original = ModelRequest(model=SimpleNamespace(), messages=[HumanMessage(content="先看目录")], tools=tools, runtime=runtime())
            handler = AsyncMock()
            await VssagDirectoryMiddleware().awrap_model_call(original, handler)
            assert "/home/mnt/knowledge/vssag/" in str(handler.call_args.args[0].messages)
            assert not any(isinstance(message, (AIMessage, ToolMessage)) for message in handler.call_args.args[0].messages)
            session_before = await pool.get_session("vssag", "alice:thread-1", {})
            for index in range(2):
                result = await graph.ainvoke(
                    {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "vssag_build_vfs",
                                        "id": f"pooled-{index}",
                                        "args": {},
                                    }
                                ],
                            )
                        ]
                    },
                    context=runtime().context,
                )
                message = result["messages"][-1]
                assert isinstance(message, ToolMessage)
                assert message.tool_call_id == f"pooled-{index}"
                assert message.status == "success"
                assert "/home/mnt/knowledge/vssag/" in json.dumps(message.content)
                assert await pool.get_session("vssag", "alice:thread-1", {}) is session_before
    finally:
        await pool.close_all()
