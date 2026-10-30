"""复用已完成的三题验证恢复链路，后台续接冻结采集并自动生成四组指标。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def validate_provider_workflow(pilot: Path) -> dict:
    """验收服务与采集链路，检索行为保留为业务观测。Args: pilot。Returns: 验收结果。"""
    from deerflow_evidence import audit_prediction
    from eval_common import RETRIEVAL_TOOLS, digest, read_json, skill_hashes, utc_now, write_json
    from evaluation_evidence import response_payload
    from prepare_dataset import build_prompt

    config = read_json(pilot / "capture_config.json")
    archived = pilot / "_private/frozen_evaluator"
    if config["code_hashes"] != {p.name: digest(p.read_bytes()) for p in sorted(archived.glob("*.py"))}:
        raise ValueError("三题原采集代码归档发生变化")
    for relative, expected in config["input_hashes"].items():
        path = (archived / relative).resolve()
        if not path.is_relative_to(archived.resolve()) or digest(path.read_bytes()) != expected:
            raise ValueError("三题原采集输入归档发生变化")
    if digest((pilot / "questions.jsonl").read_bytes()) != config["questions_sha256"] or digest((pilot / "_private/manifest.json").read_bytes()) != config["manifest_sha256"]:
        raise ValueError("三题题面或题号映射哈希发生变化")
    native = config["native_skill"]
    if skill_hashes(Path(native["source_directory"])) != native["files"]:
        raise ValueError("DeerFlow原生Skill已变化，不能混合恢复结果")
    if read_json(pilot / "pipeline_status.json").get("status") != "complete":
        raise ValueError("同三题尚未自然完成和评分")
    frozen = read_json(pilot / "predictions_frozen.json")["prediction_hashes"]
    questions = [json.loads(line) for line in (pilot / "questions.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = []
    for question in questions:
        case_id = question["case_id"]
        path = pilot / "predictions" / (case_id + ".json")
        prediction_sha = digest(path.read_bytes())
        if prediction_sha != frozen[case_id]:
            raise ValueError("三题预测与冻结哈希不一致")
        record = read_json(path)
        trace = pilot / "traces" / case_id
        messages = read_json(trace / "request.json").get("input", {}).get("messages", [])
        sql = []
        retrieval = []
        for call in record["tool_calls"]:
            payload = response_payload(call["result"])
            error = call.get("is_error") or call.get("isError") or call["result"].get("isError") or payload.get("isError")
            if call["tool"] == "sql_execute" and not error and payload.get("ok") is True and payload.get("executed") is True and payload.get("status") in {"ok", "empty"} and not payload.get("error"):
                sql.append(call)
            if call["tool"] in RETRIEVAL_TOOLS and not error and payload.get("status") in {"ok", "success"}:
                retrieval.append(call)
        checks = {
            "exact_prompt": len(messages) == 1 and messages[0].get("content") == build_prompt(question, config),
            "natural_success": record["run_status"] == "success" and not record.get("stop_reason") and read_json(trace / "terminal.json").get("terminal_confirmed") is True,
            "final_answer_present": bool(record.get("answer_text", "").strip()),
            "native_skill_activation_verified": record.get("skill_activation", {}).get("verified") is True,
            "capture_complete": record["capture"]["complete"] and not record["capture"].get("issues"),
            "raw_trace_replay_verified": audit_prediction(pilot, record),
        }
        rows.append(
            {
                "case_id": case_id,
                "thread_id": record["thread_id"],
                "run_id": record["run_id"],
                "prediction_sha256": prediction_sha,
                "checks": checks,
                "passed": all(checks.values()),
                "business_observations": {"successful_retrieval_calls": len(retrieval), "mcp_calls": len(record["tool_calls"]), "successful_sql_calls": len(sql), "elapsed_seconds": record["elapsed_seconds"]},
            }
        )
    unique = len({r["thread_id"] for r in rows}) == len(rows)
    actual_sql = sum(r["business_observations"]["successful_sql_calls"] for r in rows)
    actual_retrieval = sum(r["business_observations"]["successful_retrieval_calls"] for r in rows)
    result = {
        "validated_at_utc": utc_now(),
        "validation_kind": "provider_recovery_workflow",
        "same_three_conversations_no_resubmission": True,
        "tested_cases": len(rows),
        "independent_threads": unique,
        "successful_sql_calls": actual_sql,
        "successful_retrieval_calls": actual_retrieval,
        "cases": rows,
        "prior_strict_validation": str(pilot / "workflow_validation.json"),
        "passed": len(rows) == 3 and unique and all(r["passed"] for r in rows) and actual_sql > 0 and actual_retrieval > 0 and (pilot / "report.md").is_file(),
        "note": "服务恢复门槛核验真实链路，不要求每题都检索或业务成功；未检索为业务观测并按原规则评分。旧严格验收及三题预测保持原样。",
    }
    output = pilot / "provider_workflow_validation.json"
    if output.exists():
        previous = read_json(output)
        if [(r["case_id"], r["prediction_sha256"]) for r in previous["cases"]] != [(r["case_id"], r["prediction_sha256"]) for r in rows] or previous["passed"] != result["passed"]:
            raise ValueError("服务恢复验收的原始证据发生变化")
        return previous
    write_json(output, result, exclusive=True)
    return result


def worker(run_dir: Path) -> None:
    """执行串行恢复和最终对照，30秒心跳，不逐题读取新标签。Args: run_dir。Returns: 无。"""
    # 独立控制器优先读取自身报告代码，采集依赖只来自正式轮冻结目录。
    sys.path.insert(1, str(run_dir / "_private/frozen_evaluator"))
    from comparison_metrics import write_comparison
    from eval_common import read_json, utc_now, write_json
    from provider_recovery import combined_report
    from start_evaluation import start
    from status_evaluation import process_alive

    config = read_json(run_dir / "suite_config.json")
    pilot = Path(config["pilot_run"])
    state = {
        "pid": os.getpid(),
        "started_at_utc": utc_now(),
        "status": "validating_same_three_pilot_runs",
        "pilot_run": str(pilot),
        "formal_run": str(run_dir),
        "planned_cases": config["planned_cases"],
        "controller_kind": "provider_recovery_with_comparison_metrics",
    }
    write_json(run_dir / "suite_status.json", state)
    try:
        validation = validate_provider_workflow(pilot)
        if not validation["passed"]:
            raise RuntimeError("同三题的服务或采集流程验收未通过，保持待处理")
        state.update(status="pilot_passed", workflow_validation=str(pilot / "provider_workflow_validation.json"), pilot_passed_at_utc=utc_now())
        write_json(run_dir / "suite_status.json", state)
        print(json.dumps({"event": "provider_workflow_passed", **state}, ensure_ascii=False), flush=True)
        initial = write_comparison(run_dir)
        state["comparison_report"] = initial["report"]
        pipeline_path = run_dir / "pipeline_status.json"
        current = read_json(pipeline_path) if pipeline_path.exists() else {}
        launcher = read_json(run_dir / "launcher.json") if (run_dir / "launcher.json").exists() else {}
        if current.get("status") != "complete" and not process_alive(launcher.get("pid")):
            launcher = start(run_dir, resume=True)
        state.update(status="formal_running", child_pid=launcher.get("pid"), updated_at_utc=utc_now())
        write_json(run_dir / "suite_status.json", state)
        heartbeat, reported_valid = 0.0, initial["new_valid_predictions"]
        while True:
            current = read_json(pipeline_path) if pipeline_path.exists() else {}
            supervisor_path = run_dir / "supervisor_status.json"
            supervisor = read_json(supervisor_path) if supervisor_path.exists() else {}
            child_alive = process_alive(launcher.get("pid"))
            if current.get("status") == "complete":
                break
            if supervisor.get("status") == "needs_attention" or not child_alive:
                raise RuntimeError("冻结采集器需要处理或已退出：" + current.get("error", supervisor.get("error", "见批次状态")))
            if time.monotonic() - heartbeat >= 30:
                recovery_path = run_dir / "recovery_status.json"
                recovery = read_json(recovery_path) if recovery_path.exists() else {}
                valid = recovery.get("valid_predictions", 0)
                if valid != reported_valid:
                    reported_valid = write_comparison(run_dir)["new_valid_predictions"]
                state.update(updated_at_utc=utc_now(), current_case_id=recovery.get("case_id"), recovery_state=recovery.get("status"), new_valid_predictions=valid, pending_cases=config["planned_cases"] - valid, child_alive=child_alive)
                write_json(run_dir / "suite_status.json", state)
                print(json.dumps({"event": "provider_suite_heartbeat", **state}, ensure_ascii=False), flush=True)
                heartbeat = time.monotonic()
            time.sleep(5)
        combined_report(run_dir, final=True)
        final = write_comparison(run_dir, final=True)
        state.update(status="complete", finished_at_utc=utc_now(), comparison_report=final["report"], pending_cases=0, new_valid_predictions=config["planned_cases"])
        write_json(run_dir / "suite_status.json", state)
        print(json.dumps({"event": "provider_suite_complete", **state}, ensure_ascii=False), flush=True)
    except BaseException as error:
        state.update(status="needs_attention", updated_at_utc=utc_now(), error_type=type(error).__name__, error=str(error), evaluation_stopped=True)
        write_json(run_dir / "suite_status.json", state)
        print(json.dumps({"event": "provider_suite_stopped", **state}, ensure_ascii=False), flush=True)
        raise


def launch(run_dir: Path) -> dict:
    """归档新控制器并隐藏启动，保留原控制器和采集器。Args: run_dir。Returns: 启动元数据。"""
    from eval_common import digest, read_json, utc_now, write_json
    from status_evaluation import process_alive

    if not os.environ.get("VFSSQL_EVAL_EMAIL") or not os.environ.get("VFSSQL_EVAL_PASSWORD"):
        raise ValueError("凭据需通过临时环境变量传入，不写文件")
    if not read_json(run_dir / "suite_config.json").get("recover_infrastructure"):
        raise ValueError("只允许接管用户授权的服务故障恢复批次")
    previous = read_json(run_dir / "suite_status.json")
    if process_alive(previous.get("pid")):
        raise ValueError("既有套件控制器仍存活，拒绝重复启动")
    root = Path(__file__).resolve().parent
    sources = {name: (root / name).read_bytes() for name in ["recovery_suite.py", "comparison_metrics.py"]}
    signature = digest(b"".join(sources.values()))
    snapshot = run_dir / "_private/recovery_controllers" / signature[:16]
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        target = snapshot / name
        if not target.exists():
            with target.open("xb") as file:
                file.write(content)
        if target.read_bytes() != content:
            raise ValueError("控制器代码快照不一致")
    prior = snapshot / "prior_suite_status.json"
    if not prior.exists():
        write_json(prior, previous, exclusive=True)
    command = [sys.executable, "-X", "utf8", "-u", "-B", str(snapshot / "recovery_suite.py"), "--worker", "--run-dir", str(run_dir)]
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    kwargs = {"cwd": str(root), "stdin": subprocess.DEVNULL, "close_fds": True, "env": os.environ.copy()}
    with (run_dir / "suite.log").open("ab") as stdout, (run_dir / "suite_error.log").open("ab") as stderr:
        try:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, creationflags=flags | (subprocess.CREATE_BREAKAWAY_FROM_JOB if os.name == "nt" else 0), **kwargs)
        except OSError as error:
            if os.name != "nt" or error.winerror != 5:
                raise
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, creationflags=flags, **kwargs)
    result = {
        "pid": process.pid,
        "started_at_utc": utc_now(),
        "run_dir": str(run_dir),
        "controller_sha256": signature,
        "snapshot": str(snapshot),
        "source_hashes": {name: digest(data) for name, data in sources.items()},
        "same_pilot_conversations": True,
        "no_capture_code_changes": True,
        "status_file": "suite_status.json",
        "log": "suite.log",
        "comparison_report": "comparison_metrics/report.md",
    }
    write_json(run_dir / "recovery_suite_launcher.json", result)
    return result


def main() -> None:
    """启动或运行恢复控制器。Args: 命令行。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve(strict=True)
    if args.worker:
        worker(run_dir)
    else:
        print(json.dumps(launch(run_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
