"""评估目录、冻结配置与不可覆盖写入的公共函数。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MODEL = "Qwen3.8-27B-FP8-Local-cloud"
DATASET_SHA256 = "e6d2778929830da9d76ca09f981789d3929692baeeaf63964f477d28734bcae4"
BUDGETS = {"timeout_seconds": 1200, "max_tool_calls": 24, "max_sql_executions": 4}
NATIVE_MODE = "native_question_only"
EXPLICIT_MODE = "native_explicit_skill"
NATIVE_MODES = {NATIVE_MODE, EXPLICIT_MODE}
RETRIEVAL_TOOLS = {"vfs_find_file", "vfs_grep_file", "vfs_grep", "vfs_cat_read", "vfs_sed", "vfs_bash_execute", "search_sql_schema", "inspect_sql_schema"}
EXPLICIT_SKILL_PREFIX = "/vfssql-retrieval\n\n必须使用 vfssql-retrieval skill。\n\n"
NATIVE_BUDGETS = {**BUDGETS, "timeout_seconds": None, "enforce_tool_limits": False, "require_workspace": False}
CONTEXT = {"model_name": MODEL, "mode": "ultra", "thinking_enabled": True, "is_plan_mode": True, "subagent_enabled": True, "reasoning_effort": "high"}


def utc_now() -> str:
    """返回带时区的 UTC 时刻。Args: 无。Returns: ISO 字符串。"""
    return datetime.now(UTC).isoformat()


def digest(data: bytes) -> str:
    """计算原始字节摘要。Args: data 原始字节。Returns: SHA-256。"""
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    """读取 UTF-8 JSON。Args: path 文件。Returns: 解码对象。"""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
    """写 JSON；冻结产物可禁止覆盖。Args: path/value/exclusive。Returns: 无。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if exclusive:
        with path.open("x", encoding="utf-8", newline="\n") as file:
            file.write(text)
    else:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)


def append_json(path: Path, value: Any) -> None:
    """追加一条接收记录。Args: path/value。Returns: 无。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        file.flush()


def code_hashes() -> dict[str, str]:
    """冻结采集与评分代码。Args: 无。Returns: 文件哈希表。"""
    return {p.name: digest(p.read_bytes()) for p in sorted(ROOT.glob("*.py"))}


def git_revision() -> str | None:
    """读取当前仓库提交。Args: 无。Returns: 提交或 null。"""
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def check_frozen(run_dir: Path) -> dict:
    """核对运行输入及代码未改变。Args: run_dir 目录。Returns: 冻结配置。"""
    config = read_json(run_dir / "capture_config.json")
    if config["code_hashes"] != code_hashes():
        raise ValueError("采集或评分代码变化，须建立新运行目录")
    for relative, expected in config["input_hashes"].items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or digest(path.read_bytes()) != expected:
            raise ValueError(f"冻结输入变化：{relative}")
    if digest((run_dir / "questions.jsonl").read_bytes()) != config["questions_sha256"]:
        raise ValueError("题面哈希发生变化")
    if digest((run_dir / "_private/manifest.json").read_bytes()) != config["manifest_sha256"]:
        raise ValueError("私有题号映射发生变化")
    native = config.get("native_skill")
    if native and skill_hashes(Path(native["source_directory"])) != native["files"]:
        raise ValueError("DeerFlow 原生 Skill 在本轮运行中变化，须新建轮次")
    return config


def skill_hashes(directory: Path) -> dict[str, str]:
    """冻结原生技能包文件。Args: directory 技能目录。Returns: 相对路径和摘要。"""
    if not (directory / "SKILL.md").is_file():
        raise ValueError("原生 Skill 目录缺少 SKILL.md")
    return {p.relative_to(directory).as_posix(): digest(p.read_bytes()) for p in sorted(directory.rglob("*")) if p.is_file() and not {"__pycache__", ".git"} & set(p.relative_to(directory).parts)}
