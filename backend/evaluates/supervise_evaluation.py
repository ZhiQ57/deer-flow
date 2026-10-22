"""监督冻结评估流水线；只在知识调用明确未执行时自动续跑，不重做样本。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from pathlib import Path

LEARNING_STOP = "检测到知识学习调用，已停止批次"


def learning_was_rejected(record: dict) -> bool:
    """核验所有学习调用均在参数验证阶段被拒绝。Args: 原预测。Returns: 能否安全续跑。"""
    from evaluation_evidence import response_payload

    calls = [c for c in record.get("tool_calls", []) if c.get("tool") == "sql_learn"]
    if not calls:
        return False
    for call in calls:
        payload = response_payload(call.get("result", {}))
        text = payload.get("content", "")
        if not isinstance(text, str):
            return False
        if payload.get("ok") is True or payload.get("applied") is True or any(payload.get(k) for k in ["change_id", "evidence_id", "edge_id"]):
            return False
        if not call.get("is_error") or payload.get("isError") is not True or "McpError: invalid tool arguments; use the advertised input schema" not in text:
            return False
    return True


def record_safe_continuation(run_dir: Path, handled: set[str]) -> str:
    """审计最后一题再记录续跑原因，原预测不改写。Args: run_dir/handled。Returns: 题号。"""
    from deerflow_evidence import audit_prediction
    from eval_common import append_json, digest, read_json, utc_now

    state = read_json(run_dir / "pipeline_status.json")
    if state.get("error_type") != "GatewayFailure" or state.get("error") != LEARNING_STOP:
        raise RuntimeError("后台停止原因不是可自动恢复的参数验证拒绝")
    progress = (run_dir / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    case_id = json.loads(progress[-1])["case_id"]
    if not re.fullmatch(r"case-\d{4}", case_id) or case_id in handled:
        raise RuntimeError("同题重复停批或题号非法，保留现场检查")
    path = run_dir / "predictions" / (case_id + ".json")
    record = read_json(path)
    terminal = read_json(run_dir / "traces" / case_id / "terminal.json")
    if not terminal.get("terminal_confirmed") or record.get("run_status") not in {"success", "error", "interrupted", "timeout"}:
        raise RuntimeError("前题终态未确认，不能续跑后题")
    if not record.get("capture", {}).get("complete") or not learning_was_rejected(record) or not audit_prediction(run_dir, record):
        raise RuntimeError("学习调用未确认全部在参数验证阶段拒绝，保留停批现场")
    append_json(
        run_dir / "supervisor_incidents.jsonl",
        {
            "at_utc": utc_now(),
            "case_id": case_id,
            "reason": "learning_arguments_rejected_before_execution",
            "action": "retain_original_failure_and_continue_next_case",
            "prediction_sha256": digest(path.read_bytes()),
            "original_pipeline_status": state,
        },
    )
    handled.add(case_id)
    print(json.dumps({"event": "safe_continuation", "case_id": case_id, "reason": "learning_arguments_rejected_before_execution", "prediction_preserved": True}, ensure_ascii=False), flush=True)
    return case_id


def supervise(run_dir: Path) -> None:
    """调用原冻结流水线并输出存活心跳。Args: run_dir。Returns: 无。"""
    frozen = run_dir / "_private/frozen_evaluator"
    sys.path.insert(0, str(frozen))
    from eval_common import read_json, utc_now, write_json
    from run_pipeline import pipeline

    config = read_json(run_dir / "capture_config.json")
    if config.get("completion_policy") != "wait_for_natural_terminal":
        raise ValueError("监督器只运行无限等待协议")
    handled: set[str] = set()
    state = {"pid": os.getpid(), "started_at_utc": utc_now(), "status": "starting", "continuations": 0}
    lock, stopped = threading.Lock(), threading.Event()

    def update(**fields) -> None:
        """串行保存监督状态。Args: fields。Returns: 无。"""
        with lock:
            state.update(fields, updated_at_utc=utc_now())
            write_json(run_dir / "supervisor_status.json", state)

    def heartbeat() -> None:
        """每 30 秒输出采集进程存活信息。Args: 无。Returns: 无。"""
        while not stopped.wait(30):
            count = len(list((run_dir / "predictions").glob("*.json")))
            update(predictions_frozen=count)
            print(json.dumps({"event": "supervisor_heartbeat", "at_utc": utc_now(), "pid": os.getpid(), "predictions_frozen": count, "planned_cases": config["planned_cases"]}, ensure_ascii=False), flush=True)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        old = read_json(run_dir / "pipeline_status.json") if (run_dir / "pipeline_status.json").exists() else {}
        if old.get("status") == "needs_attention" and old.get("error") == LEARNING_STOP:
            record_safe_continuation(run_dir, handled)
        while True:
            update(status="running", continuations=len(handled))
            try:
                pipeline(run_dir)
                update(status="complete", finished_at_utc=utc_now())
                return
            except RuntimeError as error:
                if str(error) != LEARNING_STOP:
                    raise
                record_safe_continuation(run_dir, handled)
    except BaseException as error:
        update(status="needs_attention", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        stopped.set()
        thread.join(timeout=2)


def main() -> None:
    """监督启动入口。Args: 命令行参数。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    supervise(args.run_dir.resolve())


if __name__ == "__main__":
    main()
