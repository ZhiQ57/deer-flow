"""只读查看后台进程、已创建对话数和已冻结预测数，不需要登录凭据。"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path

from eval_common import read_json


def process_alive(pid: int | None) -> bool:
    """安全检查进程，不向 Windows 进程发送信号。Args: pid。Returns: 存活状态。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def status(run_dir: Path) -> dict:
    """查看当前轮次。Args: run_dir。Returns: 不含题目及答案的状态。"""
    config = read_json(run_dir / "capture_config.json")
    state_path = run_dir / "pipeline_status.json"
    state = read_json(state_path) if state_path.exists() else {"status": "starting"}
    suite_path = run_dir / "suite_status.json"
    suite = read_json(suite_path) if suite_path.exists() else None
    if suite and not state_path.exists():
        state = {"status": suite["status"], "pid": suite.get("pid"), "phase": "three_case_workflow_validation"}
    supervisor_path = run_dir / "supervisor_status.json"
    supervisor = read_json(supervisor_path) if supervisor_path.exists() else None
    recovery_path = run_dir / "recovery_status.json"
    recovery = read_json(recovery_path) if recovery_path.exists() else None
    if "pid" not in state and (run_dir / "launcher.json").exists():
        state["pid"] = read_json(run_dir / "launcher.json")["pid"]
    current = None
    log = run_dir / "console.log"
    if log.exists():
        for line in reversed(log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]):
            try:
                value = json.loads(line)
                if "case_id" in value:
                    current = {k: value[k] for k in ["case_id", "status", "finished", "elapsed_seconds", "mcp_observed", "sql_observed", "mcp_calls"] if k in value}
                    break
            except ValueError:
                continue
    return {
        "run_dir": str(run_dir),
        **state,
        "process_alive": process_alive(state.get("pid")),
        "planned_cases": config["planned_cases"],
        "evaluation_mode": config.get("evaluation_mode", "explicit_text_injection"),
        "concurrency": config.get("concurrency", 1),
        "completion_policy": config.get("completion_policy", "legacy_budget_cancellation"),
        "threads_created": len(list((run_dir / "traces").glob("*/admission.json"))),
        "predictions_frozen": len(list((run_dir / "predictions").glob("*.json"))),
        "current": current,
        "report_exists": (run_dir / "report.md").exists(),
        "supervisor": {**supervisor, "process_alive": process_alive(supervisor.get("pid"))} if supervisor else None,
        "suite": {**suite, "process_alive": process_alive(suite.get("pid"))} if suite else None,
        "pilot_predictions": len(list((Path(suite["pilot_run"]) / "predictions").glob("*.json"))) if suite else None,
        "provider_recovery": recovery,
    }


def main() -> None:
    """只读状态入口。Args: 命令行参数。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(status(args.run_dir.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
