"""启动脱离当前终端的自动评估进程；完成后自动评分并生成报告。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from eval_common import NATIVE_MODES, ROOT, digest, read_json, utc_now, write_json
from prepare_dataset import build_prompt
from prepare_dataset import prepare
from status_evaluation import process_alive


def adopt_capture(run_dir: Path, source: Path) -> None:
    """沿用既有原题对话及原始证据，不重新答题。Args: 新旧运行目录。Returns: 无。"""
    old, new = read_json(source / "capture_config.json"), read_json(run_dir / "capture_config.json")
    if old.get("evaluation_mode") not in NATIVE_MODES or old.get("evaluation_mode") != new.get("evaluation_mode") or old.get("skill_loading") != new.get("skill_loading"):
        raise ValueError("只能接管题面与原生 Skill 激活协议一致的对话")
    for key in ["questions_sha256", "dataset_sha256", "context", "native_skill"]:
        if old[key] != new[key]:
            raise ValueError("接管对话的题面、模型或 Skill 版本不同")
    state = read_json(source / "pipeline_status.json")
    if process_alive(state.get("pid")):
        raise ValueError("原采集进程仍在运行，不能同时采集同一会话")
    questions = {q["case_id"]: q["question"] for q in [json.loads(line) for line in (run_dir / "questions.jsonl").read_text(encoding="utf-8").splitlines()]}
    adopted = []
    for admission_path in sorted((source / "traces").glob("*/admission.json")):
        trace = admission_path.parent
        request = read_json(trace / "request.json")
        messages = request.get("input", {}).get("messages", [])
        expected_prompt = build_prompt({"case_id": trace.name, "question": questions[trace.name]}, new)
        if len(messages) != 1 or messages[0].get("type") != "human" or messages[0].get("content") != expected_prompt:
            raise ValueError("既有会话输入与该轮冻结提示不一致")
        admission = read_json(admission_path)
        if not admission.get("run_id"):
            raise ValueError("既有对话尚未确认受理，不能自动接管")
        terminal_path = trace / "terminal.json"
        if terminal_path.exists() and read_json(terminal_path).get("stop_reason"):
            raise ValueError("既有题目被主动停止或采集失败，不能通过接管重做评分")
        # 原证据逐字复制并记录源摘要，接管后仍查询同一 Gateway run。
        source_hashes = {p.relative_to(trace).as_posix(): digest(p.read_bytes()) for p in sorted(trace.rglob("*")) if p.is_file()}
        shutil.copytree(trace, run_dir / "traces" / trace.name)
        adopted.append({"case_id": trace.name, "thread_id": admission["thread_id"], "run_id": admission["run_id"], "source_hashes": source_hashes})
    if (source / "case_threads.jsonl").is_file():
        shutil.copyfile(source / "case_threads.jsonl", run_dir / "case_threads.jsonl")
    write_json(run_dir / "_private/adoption.json", {"adopted_at_utc": utc_now(), "source_run": str(source), "same_conversations_no_resubmission": True, "cases": adopted}, exclusive=True)


def start(run_dir: Path, *, resume: bool = False, adopt_from: Path | None = None) -> dict:
    """后台运行整轮评估。Args: run_dir/resume/adopt_from。Returns: 不含凭据的受理信息。"""
    if not os.environ.get("VFSSQL_EVAL_EMAIL") or not os.environ.get("VFSSQL_EVAL_PASSWORD"):
        raise ValueError("请在当前进程设置 VFSSQL_EVAL_EMAIL / VFSSQL_EVAL_PASSWORD")
    if run_dir.exists():
        if adopt_from:
            raise ValueError("接管必须使用新的运行目录")
        if not resume:
            raise FileExistsError("运行目录已经存在；恢复时显式指定 --resume")
        if (run_dir / "pipeline_status.json").is_file():
            old = read_json(run_dir / "pipeline_status.json")
            if old.get("status") == "complete":
                raise ValueError("本轮已经全部完成")
            if process_alive(old.get("pid")):
                raise ValueError("本轮后台进程仍在运行")
        if (run_dir / "supervisor_status.json").is_file() and process_alive(read_json(run_dir / "supervisor_status.json").get("pid")):
            raise ValueError("本轮后台监督进程仍在运行")
    else:
        prepare(run_dir)
        if adopt_from:
            adopt_capture(run_dir, adopt_from.resolve())
    frozen_root = run_dir / "_private/frozen_evaluator"
    config = read_json(run_dir / "capture_config.json")
    if config.get("completion_policy") != "wait_for_natural_terminal":
        raise ValueError("旧轮存在超时取消协议；请用 --adopt-from 接管到新的无限等待轮次")
    # 使用该轮自己的代码快照，后续修改工作区不会改变正在运行的协议。
    for relative, expected in config["code_hashes"].items():
        if digest((frozen_root / relative).read_bytes()) != expected:
            raise ValueError("归档采集代码哈希不一致")
    # 监督器单独留快照，既有采集/评分代码及预测均保持原来的冻结版本。
    supervisor_bytes = (ROOT / "supervise_evaluation.py").read_bytes()
    supervisor_sha = digest(supervisor_bytes)
    supervisor_path = run_dir / "_private/supervisors" / (supervisor_sha + ".py")
    supervisor_path.parent.mkdir(parents=True, exist_ok=True)
    if not supervisor_path.exists():
        with supervisor_path.open("xb") as target:
            target.write(supervisor_bytes)
    if digest(supervisor_path.read_bytes()) != supervisor_sha:
        raise ValueError("监督器快照哈希不一致")
    command = [sys.executable, "-X", "utf8", "-u", "-B", str(supervisor_path), "--run-dir", str(run_dir)]
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
            "supervisor_sha256": supervisor_sha,
            "supervisor_snapshot": str(supervisor_path),
        }
        write_json(run_dir / "launcher.json", result)
    return result


def main() -> None:
    """后台启动命令入口。Args: 命令行参数。Returns: 无。"""
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=ROOT / "runs" / f"{stamp}-qwen38-ultra-native-unlimited")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--adopt-from", type=Path)
    args = parser.parse_args()
    print(json.dumps(start(args.run_dir.resolve(), resume=args.resume, adopt_from=args.adopt_from), ensure_ascii=False))


if __name__ == "__main__":
    main()
