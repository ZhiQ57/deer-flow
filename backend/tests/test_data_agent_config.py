from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

from deerflow.config.agents_config import AgentConfig
from deerflow.config.extensions_config import ExtensionsConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_AGENT_DOCS_DIR = REPO_ROOT / "docs" / "agents" / "data-agent"


def test_data_agent_template_matches_custom_agent_schema() -> None:
    """校验 DataAgent 模板符合 DeerFlow 原生 custom-agent 配置结构。

    Args:
        无。

    Return:
        None。断言模板可以解析为 AgentConfig。
    """
    # 读取文档模板，确保后续复制到 .deer-flow 运行时目录后可被原生 loader 识别。
    config_data = yaml.safe_load((DATA_AGENT_DOCS_DIR / "config.yaml").read_text(encoding="utf-8"))
    agent_config = AgentConfig(**config_data)

    assert agent_config.name == "data-agent"
    assert agent_config.model == "Qwen3.6-plus"
    assert agent_config.tool_groups == ["web", "file:read", "file:write", "bash"]
    assert agent_config.skills == ["table-rag-agent", "data-analysis", "chart-visualization"]


def test_data_agent_skill_whitelist_references_installed_public_skills() -> None:
    """校验 DataAgent 白名单中的 Skill 都能在 public skills 中找到。

    Args:
        无。

    Return:
        None。断言 Skill frontmatter 名称存在。
    """
    # Skill 白名单使用 frontmatter name，而不是目录名；TableRAG 目录名为 z_sqltable-rag。
    config_data = yaml.safe_load((DATA_AGENT_DOCS_DIR / "config.yaml").read_text(encoding="utf-8"))
    expected_skill_names = set(config_data["skills"])

    discovered_skill_names: set[str] = set()
    for skill_file in (REPO_ROOT / "skills" / "public").rglob("SKILL.md"):
        content = skill_file.read_text(encoding="utf-8")
        if not content.startswith("---"):
            continue
        _, frontmatter, _ = content.split("---", 2)
        metadata = yaml.safe_load(frontmatter) or {}
        if isinstance(metadata.get("name"), str):
            discovered_skill_names.add(metadata["name"].strip())

    assert expected_skill_names <= discovered_skill_names


def test_extensions_example_contains_disabled_tablerag_mcp_server() -> None:
    """校验 extensions 示例包含默认关闭的 TableRAG MCP server。

    Args:
        无。

    Return:
        None。断言示例 JSON 可被 ExtensionsConfig 解析。
    """
    # 示例文件不能包含真实凭据，只能通过环境变量占位符注入运行时配置。
    raw = json.loads((REPO_ROOT / "extensions_config.example.json").read_text(encoding="utf-8"))
    extensions_config = ExtensionsConfig.model_validate(raw)
    tablerag = extensions_config.mcp_servers["tablerag"]

    assert tablerag.enabled is False
    assert tablerag.type == "stdio"
    assert tablerag.command == "python"
    assert tablerag.args == ["-m", "table_rag.mcp", "--transport", "stdio"]
    assert tablerag.env["TABLERAG_MCP_CONFIG"] == "$TABLERAG_CONFIG"
    assert "TABLERAG_MCP_INDEX_DSN" not in tablerag.env
    assert "TABLERAG_MCP_SOURCE_DSN" not in tablerag.env
    assert tablerag.env["TABLERAG_MCP_ALLOW_INITIALIZE"] == "false"
    assert tablerag.env["TABLERAG_MCP_ALLOW_SYNC_VALUES"] == "false"


def test_table_rag_package_is_included_in_harness_wheel() -> None:
    """校验 deerflow-harness wheel 会打包 table_rag 模块。

    Args:
        无。

    Return:
        None。断言 stdio MCP 子进程可通过 python -m table_rag.mcp 方式找到模块。
    """
    # DataAgent 原生 MCP 启动依赖模块级入口，因此 table_rag 必须进入 harness wheel。
    pyproject = tomllib.loads((REPO_ROOT / "backend" / "packages" / "harness" / "pyproject.toml").read_text(encoding="utf-8"))
    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert "deerflow" in packages
    assert "table_rag" in packages
