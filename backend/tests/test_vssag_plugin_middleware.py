"""标准插件布局与配置加载保持原样，目录行为由上下文测试约束。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import AgentMiddleware

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "mcp-extensions/vssag"
sys.path.insert(0, str(PLUGIN))


def test_mcp_aggregation_directory_and_current_docs_have_no_old_paths():
    """验证聚合目录及现行入口不再引用迁移前的示例路径。

    Args:
        无；读取无凭据的公开配置、测试入口和现行文档。
    Returns:
        无；目录缺失或公开指引仍引用旧位置时断言失败。
    """
    assert PLUGIN == ROOT / "mcp-extensions" / "vssag"
    assert (PLUGIN / "vssag_mcp_server" / "server.py").is_file()
    assert (PLUGIN / "extension" / "pyproject.toml").is_file()
    for path in ("README.md", "AGENTS.md", "backend/AGENTS.md", "config.example.yaml", "docs/guide/vssag-mcp.md", "docs/guide/used-api.md"):
        assert "examples/deerflow-vssag-mcp" not in (ROOT / path).read_text(encoding="utf-8")


def test_portable_plugin_resources_match_served_tools():
    """保留用户插件资源，DeerFlow Middleware 不冒充标准自动加载组件。"""
    portable = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    legacy = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert portable["$schema"].endswith("/plugin.schema.json")
    assert (portable["name"], portable["version"]) == (legacy["name"], legacy["version"])
    assert "middlewares" not in portable
    config = json.loads((PLUGIN / "mcp.json").read_text(encoding="utf-8"))
    assert set(config["mcpServers"]) == {"vssag"}
    assert config["mcpServers"]["vssag"]["type"] == "stdio"
    legacy_mcp = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    assert {key: value for key, value in config["mcpServers"]["vssag"].items() if key != "type"} == legacy_mcp["mcpServers"]["vssag"]
    assert portable["extensions"]["com.openai"]["hooks"] == legacy["hooks"]
    hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))
    assert set(hooks["hooks"]) == {"SessionStart"}


def test_hook_never_rewrites_or_executes_shell():
    """Hook 不重写混合 Bash，不添加目录获取的伪模型行为。"""
    spec = importlib.util.spec_from_file_location("vssag_plugin_entry_test", PLUGIN / "hooks/entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.hook_response({"hook_event_name": "PreToolUse", "tool_input": {"command": "grep foo /home/mnt/knowledge > output.txt"}}) == {}
    output = module.hook_response({"hook_event_name": "SessionStart"})
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_existing_config_loader_loads_external_middleware():
    """继续复用现有 class/kwargs loader，不新增 Harness 入口。"""
    from deerflow.agents.middlewares.configured_extensions import load_configured_extension_middlewares

    example = json.loads((PLUGIN / "extensions_config.example.json").read_text(encoding="utf-8"))
    loaded = load_configured_extension_middlewares(SimpleNamespace(extensions=SimpleNamespace(middlewares=example["middlewares"])))
    assert isinstance(loaded[0], AgentMiddleware)
    assert type(loaded[0]).__module__ == "middlewares.vssag_directory"
    assert loaded[0].release_policy_parameters()["execution"] == "host_context_before_model"


def test_root_example_documents_complete_opt_in_without_importing_optional_package():
    """根示例必须展示完整接入，但不能让默认安装导入可选包。

    Args:
        无。
    Returns:
        无；根示例与专用示例契约不一致时断言失败。
    """
    root_example = json.loads((ROOT / "extensions_config.example.json").read_text(encoding="utf-8"))
    plugin_example = json.loads((PLUGIN / "extensions_config.example.json").read_text(encoding="utf-8"))
    assert root_example["mcpServers"]["vssag"] == plugin_example["mcpServers"]["vssag"]
    assert root_example["mcpServers"]["vssag"]["enabled"] is False
    assert root_example["middlewares"] == []
    assert root_example["mcpInterceptors"] == []
    instructions = root_example["$comment"]
    assert "middlewares.vssag_directory:VssagDirectoryMiddleware" in instructions
    assert "vssag_mcp_server.interceptor:build_context_interceptor" in instructions
    assert "docs/guide/vssag-mcp.md" in instructions
    assert plugin_example["mcpServers"]["vssag"]["env"]["APP_ENV"] == "dev"


def test_documented_config_resolves_paths_and_loads_both_external_entries(tmp_path, monkeypatch):
    """用真实配置解析器和加载器核对目录中间件与身份拦截入口。

    Args:
        tmp_path: pytest 提供的隔离配置目录。
        monkeypatch: 仅为当前测试设置无凭据的路径变量。
    Returns:
        无；配置字段、变量解析或入口加载失败时断言失败。
    """
    from deerflow.agents.middlewares.configured_extensions import load_configured_extension_middlewares
    from deerflow.config.extensions_config import ExtensionsConfig
    from deerflow.reflection import resolve_variable

    example = json.loads((ROOT / "extensions_config.example.json").read_text(encoding="utf-8"))
    plugin_example = json.loads((PLUGIN / "extensions_config.example.json").read_text(encoding="utf-8"))
    example["middlewares"] = plugin_example["middlewares"]
    example["mcpInterceptors"] = plugin_example["mcpInterceptors"]
    example["mcpServers"]["vssag"]["enabled"] = True
    python_path = str(tmp_path / "service python.exe")
    config_path = str(tmp_path / "sdk config.yaml")
    monkeypatch.setenv("VSSAG_MCP_PYTHON", python_path)
    monkeypatch.setenv("VSSAG_MCP_CONFIG", config_path)
    extensions_path = tmp_path / "extensions_config.json"
    extensions_path.write_text(json.dumps(example, ensure_ascii=False), encoding="utf-8")
    parsed = ExtensionsConfig.from_file(str(extensions_path))
    server = parsed.get_enabled_mcp_servers()["vssag"]
    assert server.command == python_path
    assert server.args == ["-I", "-m", "vssag_mcp_server.server", "--config", config_path, "--root", "/home/mnt/knowledge"]
    assert server.env["APP_ENV"] == "dev"
    loaded = load_configured_extension_middlewares(SimpleNamespace(extensions=parsed))
    assert len(loaded) == 1
    assert isinstance(loaded[0], AgentMiddleware)
    assert type(loaded[0]).__name__ == "VssagDirectoryMiddleware"
    interceptor_factory = resolve_variable(parsed.model_extra["mcpInterceptors"][0])
    assert callable(interceptor_factory())


def test_full_lead_middleware_builder_preserves_runtime_order():
    """真实 lead builder 继续使用现有安全过滤与结束处理顺序。"""
    from deerflow.agents.lead_agent.agent import build_middlewares
    from deerflow.config.app_config import AppConfig
    from deerflow.config.extensions_config import ExtensionsConfig
    from deerflow.extensions.registry import ExtensionRegistry
    from deerflow.tools.builtins.tool_search import DeferredToolSetup

    config = AppConfig.model_construct()
    config.extensions = ExtensionsConfig(middlewares=["middlewares.vssag_directory:VssagDirectoryMiddleware"])
    config.memory.enabled = False
    setup = DeferredToolSetup(tool_search_tool=SimpleNamespace(), deferred_names=frozenset({"vssag_build_vfs"}), catalog_hash="test-vssag")
    chain = build_middlewares({"configurable": {"subagent_enabled": False}}, model_name=None, app_config=config, deferred_setup=setup, extensions=ExtensionRegistry().build(), available_skills=set(), owns_agent_skill_projection=False)
    names = [type(item).__name__ for item in chain]
    assert names.count("VssagDirectoryMiddleware") == 1
    assert names.index("InputSanitizationMiddleware") < names.index("VssagDirectoryMiddleware")
    assert names.index("SkillToolPolicyMiddleware") < names.index("DeferredToolFilterMiddleware") < names.index("VssagDirectoryMiddleware")
    assert names.index("VssagDirectoryMiddleware") < names.index("TerminalResponseMiddleware")


@pytest.mark.parametrize("budget", [False, 0, -1, 1.5])
def test_invalid_directory_budget_is_rejected(budget):
    """字符预算必须是正整数。"""
    from middlewares.vssag_directory import VssagDirectoryMiddleware

    with pytest.raises(ValueError):
        VssagDirectoryMiddleware(max_tree_chars=budget)


@pytest.mark.parametrize("timeout", [False, 0, -1, float("inf"), float("nan"), "30"])
def test_invalid_timeout_is_rejected(timeout):
    """总时限必须是有限正数。"""
    from middlewares.vssag_directory import VssagDirectoryMiddleware

    with pytest.raises(ValueError):
        VssagDirectoryMiddleware(timeout_seconds=timeout)
