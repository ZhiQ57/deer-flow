"""启动脱离当前终端的自动评估进程；完成后自动评分并生成报告。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from eval_common import ROOT, read_json, utc_now, write_json
from prepare_dataset import prepare
from status_evaluation import process_alive


def start(run_dir: Path, *, resume: bool = False) -> dict:
    """后台运行整轮评估。Args: run_dir/resume。Returns: 不含凭据的受理信息。"""
    if not os.environ.get("VFSSQL_EVAL_EMAIL") or not os.environ.get("VFSSQL_EVAL_PASSWORD"):
        raise ValueError("请在当前进程设置 VFSSQL_EVAL_EMAIL / VFSSQL_EVAL_PASSWORD")
    if run_dir.exists():
        if not resume:
            raise FileExistsError("运行目录已经存在；恢复时显式指定 --resume")
        if (run_dir / "pipeline_status.json").is_file():
            old = read_json(run_dir / "pipeline_status.json")
            if old.get("status") == "complete":
                raise ValueError("本轮已经全部完成")
            if process_alive(old.get("pid")):
                raise ValueError("本轮后台进程仍在运行")
    else:
        prepare(run_dir)
    frozen_root = run_dir / "_private/frozen_evaluator"
    config = read_json(run_dir / "capture_config.json")
    # 使用该轮自己的代码快照，后续修改工作区不会改变正在运行的协议。
    for relative, expected in config["code_hashes"].items():
        from eval_common import digest

        if digest((frozen_root / relative).read_bytes()) != expected:
            raise ValueError("归档采集代码哈希不一致")
    command = [sys.executable, "-X", "utf8", "-u", "-B", str(frozen_root / "run_pipeline.py"), "--run-dir", str(run_dir)]
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    kwargs = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL, "close_fds": True, "env": os.environ.copy()}
    detachment = "new_process_group"
    with (run_dir / "console.log").open("ab") as stdout, (run_dir / "error.log").open("ab") as stderr:
        try:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, creationflags=flags | (subprocess.CREATE_BREAKAWAY_FROM_JOB if os.name == "nt" else 0), **kwargs)
            detachment = "breakaway_from_job" if os.name == "nt" else "new_session"
        except OSError as error:
            if os.name != "nt" or error.winerror != 5:
                raise
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, creationflags=flags, **kwargs)
        result = {
            "pid": process.pid,
            "started_at_utc": utc_now(),
            "run_dir": str(run_dir),
            "planned_cases": config["planned_cases"],
            "detachment": detachment,
            "stdout": "console.log",
            "stderr": "error.log",
            "status_file": "pipeline_status.json",
            "report": "report.md",
        }
        write_json(run_dir / "launcher.json", result)
    return result


def main() -> None:
    """后台启动命令入口。Args: 命令行参数。Returns: 无。"""
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=ROOT / "runs" / f"{stamp}-qwen38-ultra-independent")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(json.dumps(start(args.run_dir.resolve(), resume=args.resume), ensure_ascii=False))


if __name__ == "__main__":
    main()
