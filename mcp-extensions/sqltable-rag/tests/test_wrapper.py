"""sqltable-rag 外部启动包装单元测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import yaml

SERVER_PATH = Path(__file__).parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("sqltable_rag_mcp_wrapper", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)


def test_resolve_template_uses_default_and_environment(monkeypatch) -> None:
    """启动配置应支持 `${VAR:-default}` 和环境变量覆盖。"""
    monkeypatch.delenv("TABLERAG_CONFIG", raising=False)
    assert SERVER._resolve_template("${TABLERAG_CONFIG:-tablerag.yaml}").endswith("tablerag.yaml")

    monkeypatch.setenv("TABLERAG_CONFIG", "custom-tablerag.yaml")
    assert SERVER._resolve_template("${TABLERAG_CONFIG:-tablerag.yaml}").endswith("custom-tablerag.yaml")


def test_main_applies_yaml_and_environment_overrides(monkeypatch, tmp_path: Path) -> None:
    """包装器应将独立 YAML 转成现有 TableRAG MCP Settings。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "server": {"transport": "stdio", "host": "127.0.0.1", "port": 8004},
                "tablerag": {
                    "config_path": "${TABLERAG_CONFIG:-tablerag.yaml}",
                    "index_dsn_env": "TABLERAG_MCP_INDEX_DSN",
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeSettings:
        @classmethod
        def from_env(cls):
            return cls()

        def with_overrides(self, **kwargs):
            captured.update(kwargs)
            return self

    fake_package = ModuleType("table_rag")
    fake_mcp = ModuleType("table_rag.mcp")
    fake_mcp.TableRAGMCPSettings = FakeSettings
    fake_mcp.run_server = lambda settings: captured.update(run_settings=settings)
    fake_package.mcp = fake_mcp
    monkeypatch.setitem(sys.modules, "table_rag", fake_package)
    monkeypatch.setitem(sys.modules, "table_rag.mcp", fake_mcp)
    monkeypatch.setenv("TABLERAG_MCP_CONFIG", str(tmp_path / "mounted-tablerag.yaml"))
    monkeypatch.setenv("TABLERAG_MCP_INDEX_DSN", "postgresql://index")
    monkeypatch.setenv("TABLERAG_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("TABLERAG_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("TABLERAG_MCP_PORT", "9004")
    monkeypatch.setattr(sys, "argv", ["server.py", "--config", str(config_path)])

    SERVER.main()

    assert captured["config_path"] == str(tmp_path / "mounted-tablerag.yaml")
    assert captured["index_dsn"] == "postgresql://index"
    assert captured["transport"] == "streamable-http"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9004
    assert "run_settings" in captured
