from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    """定位 DeerFlow 仓库根目录。

    Args:
        无。

    Return:
        DeerFlow 仓库根目录。
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "AGENTS.md").is_file() and (candidate / "backend" / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("无法定位 DeerFlow 仓库根目录。")


def test_data_agent_follows_deerflow_sdk_module_boundaries() -> None:
    """校验 DataAgent 实现按 DeerFlow SDK 目录边界拆分。

    Args:
        无。

    Return:
        None。
    """
    dev_root = _repo_root() / "backend" / "packages" / "harness" / "deerflow-dev"
    sdk_root = _repo_root() / "backend" / "packages" / "harness" / "deerflow"
    required_files = {
        "agents/data_agent/agent.py",
        "agents/data_agent/prompt.py",
        "agents/middlewares/data_agent_turn_reset_middleware.py",
        "agents/middlewares/data_agent_orchestration_middleware.py",
        "agents/thread_state.py",
        "tools/builtins/data_validate_sql_tool.py",
        "tools/builtins/data_execute_sql_tool.py",
        "tools/builtins/chart_spec_tool.py",
        "tools/sql_validation.py",
        "tools/database.py",
        "tools/chart_spec.py",
        "subagents/builtins/__init__.py",
    }
    removed_files = {
        "agents/data_agent/chart.py",
        "agents/data_agent/database.py",
        "agents/data_agent/middleware.py",
        "agents/data_agent/sql_validation.py",
        "agents/data_agent/state.py",
        "agents/data_agent/tools.py",
        "agents/middlewares/query_context_middleware.py",
        "middleware/query_context_middleware.py",
        "tools/builtins/query_context_tool.py",
        "tools/query_context.py",
    }

    assert (sdk_root / "tools" / "builtins" / "entity_extract_tool.py").is_file()
    assert (sdk_root / "tools" / "builtins" / "query_labels_tool.py").is_file()
    assert (sdk_root / "agents" / "middlewares" / "query_labels_middleware.py").is_file()
    assert all((dev_root / path).is_file() for path in required_files)
    assert all(not (dev_root / path).exists() for path in removed_files)
