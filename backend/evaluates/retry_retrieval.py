"""先用三题验证显式 Skill 自动评估流程，再后台复测缺少检索的样本。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def archive_source(source: Path) -> Path:
    """将完整旧轮移入归档，清除其活动轨迹入口。Args: source。Returns: 可审计归档路径。"""
    from eval_common import ROOT, read_json, utc_now, write_json

    runs = (ROOT / "runs").resolve()
    source = source.resolve(strict=True)
    if source.parent != runs or not source.is_relative_to(runs) or source.is_symlink():
        raise ValueError("仅允许归档评估 runs 下直接命名的完整旧轮")
    if read_json(source / "pipeline_status.json").get("status") != "complete":
        raise ValueError("源评估尚未完成，不能移动活动会话的证据")
    archive_root = (runs / "_archive").resolve()
    # 短归档名避免 Windows 260 字符限制，原完整名称保存在 archival.json。
    target = (archive_root / (source.name.split("-", 1)[0] + "-native")).resolve()
    if not target.is_relative_to(archive_root) or not target.is_relative_to(runs) or target.exists():
        raise ValueError("归档目标越界或已经存在")
    archive_root.mkdir(exist_ok=True)
    # 移动前已核验两个绝对路径位于用户指定评估目录，不使用递归删除。
    mode = "directory_move"
    try:
        source.rename(target)
    except PermissionError:
        # Windows 中打开的报告可能锁住父目录；冻结文件用硬链接快照保留，随后只清理授权样本。
        shutil.copytree(source, target, copy_function=os.link, symlinks=True)
        mode = "immutable_hardlink_snapshot"
    write_json(target / "archival.json", {"archived_at_utc": utc_now(), "original_path": str(source), "archived_path": str(target), "snapshot_mode": mode, "reason": "用户要求缺检索样本清除活动轨迹后显式 Skill 重测；完整旧轮仅保留审计和81题对照"}, exclusive=True)
    return target


def clear_old_selected(source: Path, archived: Path, run_dir: Path) -> None:
    """在完整归档后删除授权样本的旧活动轨迹和预测。Args: source/archived/run_dir。Returns: 无。"""
    from eval_common import ROOT, digest, read_json, utc_now, write_json

    if not source.exists():
        return
    source, archived = source.resolve(), archived.resolve()
    runs = (ROOT / "runs").resolve()
    if source.parent != runs or not archived.is_relative_to(runs / "_archive"):
        raise ValueError("旧轨迹清理路径越界")
    selection = read_json(run_dir / "_private/retry_selection.json")
    frozen = read_json(archived / "predictions_frozen.json")["prediction_hashes"]
    for case in selection["selected_cases"]:
        case_id = case["case_id"]
        if not case_id.startswith("case-") or not case_id[5:].isdigit() or len(case_id) != 9:
            raise ValueError("清理题号非法")
        prediction = (source / "predictions" / f"{case_id}.json").resolve()
        trace = (source / "traces" / case_id).resolve()
        if not prediction.is_relative_to(source / "predictions") or not trace.is_relative_to(source / "traces"):
            raise ValueError("样本清理路径越界")
        if digest((archived / "predictions" / f"{case_id}.json").read_bytes()) != frozen[case_id]:
            raise ValueError("归档预测哈希异常，拒绝清理活动轨迹")
        # 所有路径均已限定在此旧轮内；归档使用独立目录名，unlink 不修改归档硬链接的内容。
        if trace.exists():
            shutil.rmtree(trace)
        prediction.unlink(missing_ok=True)
    write_json(source / "active_data_cleanup.json", {"cleaned_at_utc": utc_now(), "archive": str(archived), "removed_old_cases": len(selection["selected_cases"]), "retained_cases": len(selection["retained_cases"]), "note": "此路径仅保留原81题的活动数据；完整324题历史报告与冻结预测应从归档路径读取。"})


def launch_child(run_dir: Path) -> dict:
    """启动或接续单个隐藏批次，不重复启动存活进程。Args: run_dir。Returns: 启动或既有状态。"""
    from eval_common import read_json
    from start_evaluation import start
    from status_evaluation import process_alive

    for name in ["pipeline_status.json", "supervisor_status.json", "launcher.json"]:
        path = run_dir / name
        if path.exists():
            state = read_json(path)
            if state.get("status") == "complete" or process_alive(state.get("pid")):
                return state
    return start(run_dir, resume=True)


def wait_child(run_dir: Path, suite: Path, phase: str, state: dict) -> dict:
    """等待独立批次自然完成并记录心跳。Args: 批次/套件/阶段/状态。Returns: 完成状态。"""
    from eval_common import read_json, utc_now, write_json
    from status_evaluation import process_alive

    last_heartbeat = 0.0
    while True:
        path = run_dir / "pipeline_status.json"
        status = read_json(path) if path.exists() else {}
        launcher = read_json(run_dir / "launcher.json")
        if status.get("status") == "complete":
            return status
        if status.get("status") == "needs_attention":
            supervisor_path = run_dir / "supervisor_status.json"
            supervisor = read_json(supervisor_path) if supervisor_path.exists() else {}
            if supervisor.get("status") == "needs_attention" or not process_alive(launcher.get("pid")):
                raise RuntimeError(f"{phase}停止：{status.get('error', '见批次状态文件')}")
        if not process_alive(launcher.get("pid")):
            raise RuntimeError(f"{phase}后台进程已退出且批次未完成")
        if time.monotonic() - last_heartbeat >= 30:
            state.update(status=phase, updated_at_utc=utc_now(), current_run=str(run_dir), current_predictions=len(list((run_dir / "predictions").glob("*.json"))))
            write_json(suite / "suite_status.json", state)
            print(json.dumps({"event": "retry_suite_heartbeat", **state}, ensure_ascii=False), flush=True)
            last_heartbeat = time.monotonic()
        time.sleep(5)


def validate_pilot(pilot: Path) -> dict:
    """验收三题的真实运行链路，不以题目业务分数筛选。Args: pilot。Returns: 流程验收报告。"""
    from deerflow_evidence import audit_prediction
    from eval_common import RETRIEVAL_TOOLS, digest, read_json, utc_now, write_json
    from evaluation_evidence import response_payload
    from prepare_dataset import build_prompt

    config = read_json(pilot / "capture_config.json")
    questions = [json.loads(line) for line in (pilot / "questions.jsonl").read_text(encoding="utf-8").splitlines()]
    rows, successful_sql = [], 0
    for question in questions:
        case_id = question["case_id"]
        record = read_json(pilot / "predictions" / (case_id + ".json"))
        request = read_json(pilot / "traces" / case_id / "request.json")
        messages = request.get("input", {}).get("messages", [])
        retrieval = [call for call in record["tool_calls"] if call["tool"] in RETRIEVAL_TOOLS]
        retrieval_ok = any(not call.get("is_error") and response_payload(call["result"]).get("status") in {"ok", "success"} for call in retrieval)
        sql_ok = sum(not call.get("is_error") and response_payload(call["result"]).get("ok") is True and response_payload(call["result"]).get("executed") is True for call in record["tool_calls"] if call["tool"] == "sql_execute")
        successful_sql += sql_ok
        checks = {
            "exact_prompt": len(messages) == 1 and messages[0].get("content") == build_prompt(question, config),
            "natural_success": record["run_status"] == "success" and not record.get("stop_reason"),
            "final_answer_present": bool(record.get("answer_text", "").strip()),
            "skill_body_injection_verified": record.get("skill_activation", {}).get("verified") is True,
            "retrieval_called_successfully": retrieval_ok,
            "capture_complete": record["capture"]["complete"],
            "raw_trace_replay_verified": audit_prediction(pilot, record),
        }
        rows.append({"case_id": case_id, "thread_id": record["thread_id"], "run_id": record["run_id"], "checks": checks, "passed": all(checks.values()), "mcp_calls": len(record["tool_calls"]), "successful_sql_calls": sql_ok, "elapsed_seconds": record["elapsed_seconds"], "prediction_sha256": digest((pilot / "predictions" / (case_id + ".json")).read_bytes())})
    unique = len({row["thread_id"] for row in rows}) == len(rows)
    validation = {"validated_at_utc": utc_now(), "tested_cases": len(rows), "passed": len(rows) == 3 and all(row["passed"] for row in rows) and successful_sql > 0 and unique, "independent_threads": unique, "successful_sql_calls": successful_sql, "cases": rows, "scoring_report_exists": (pilot / "report.md").is_file(), "note": "验收真实请求、Skill 正文激活、工具链路与归档评分；不要求三题业务评分全部成功，不向正式题发送评分反馈。"}
    validation["passed"] = validation["passed"] and validation["scoring_report_exists"]
    write_json(pilot / "workflow_validation.json", validation)
    return validation


def comparison_report(run_dir: Path) -> None:
    """分组汇总原检索题与本次复测题，保留不同提示条件。Args: run_dir。Returns: 无。"""
    from eval_common import read_json, utc_now, write_json

    config = read_json(run_dir / "capture_config.json")
    source = Path(config["retry_source"])
    selection = read_json(run_dir / "_private/retry_selection.json")
    snapshots = sorted((source / "scoring").glob("*/scores.json"))
    old = {row["case_id"]: row for row in read_json(snapshots[-1])}
    latest = sorted((run_dir / "scoring").glob("*/scores.json"))[-1]
    new = {row["case_id"]: row for row in read_json(latest)}
    retained = [old[case["case_id"]] for case in selection["retained_cases"]]
    retried = list(new.values())
    comparison = {"generated_at_utc": utc_now(), "source_archive": str(source), "total": len(retained) + len(retried), "retained_original_retrieval_cases": len(retained), "retried_explicit_skill_cases": len(retried), "retained_primary_success": sum(row["primary_success"] for row in retained), "retried_primary_success": sum(row["primary_success"] for row in retried), "combined_primary_success": sum(row["primary_success"] for row in retained + retried), "retried_retrieval_used": sum(row["retrieval_used"] for row in retried), "retried_skill_activation_verified": sum(row["skill_activation_verified"] for row in retried), "retried_retrieval_rounds_total": sum(row["retrieval_rounds_total"] for row in retried), "retried_total_seconds": sum(row["elapsed_seconds"] for row in retried), "note": "81题沿用原问题自主发现条件，243题为缺检索子集的显式Skill复测；不是统一提示条件下重新跑完324题。"}
    comparison["combined_primary_success_rate"] = comparison["combined_primary_success"] / comparison["total"]
    write_json(run_dir / "comparison_summary.json", comparison)
    lines = ["# 缺检索样本显式 Skill 复测对照", "", "| 分组 | 题数 | 检索完整且最终执行成功 |", "| --- | ---: | ---: |", f"| 原轮已使用检索的保留组 | {len(retained)} | {comparison['retained_primary_success']} |", f"| 本次显式 Skill 复测组 | {len(retried)} | {comparison['retried_primary_success']} |", f"| 组合结果 | {comparison['total']} | {comparison['combined_primary_success']}，{comparison['combined_primary_success_rate']:.2%} |", "", comparison["note"], "", "本次243题的独立报告见 report.md，三题流程验证见 suite_config.json 指定的 pilot_run/workflow_validation.json。失败题全部保留，不重复答题择优。", ""]
    (run_dir / "comparison_report.md").write_text("\n".join(lines), encoding="utf-8")


def worker(run_dir: Path) -> None:
    """独立后台编排三题验证和正式复测。Args: run_dir。Returns: 无。"""
    from eval_common import read_json, utc_now, write_json

    suite_config = read_json(run_dir / "suite_config.json")
    pilot = Path(suite_config["pilot_run"])
    state = {"pid": os.getpid(), "started_at_utc": utc_now(), "status": "pilot_testing", "pilot_run": str(pilot), "formal_run": str(run_dir), "planned_cases": suite_config["planned_cases"]}
    write_json(run_dir / "suite_status.json", state)
    try:
        source_pilot = suite_config.get("adopt_pilot")
        if source_pilot and not (pilot / "_private/adoption.json").exists():
            from start_evaluation import adopt_capture
            from status_evaluation import process_alive

            source_path = Path(source_pilot)
            wait_child(source_path, run_dir, "waiting_for_same_three_pilot_runs", state)
            # 等既有三题自然完成并退出采集器；只重放原始证据，不另发三条模型请求。
            while process_alive(read_json(source_path / "pipeline_status.json").get("pid")):
                time.sleep(2)
            adopt_capture(pilot, source_path)
        launch_child(pilot)
        wait_child(pilot, run_dir, "pilot_testing", state)
        validation = validate_pilot(pilot)
        if not validation["passed"]:
            raise RuntimeError("三题实际流程验收未通过；正式批次尚未启动，见 workflow_validation.json")
        state.update(status="pilot_passed", pilot_passed_at_utc=utc_now(), workflow_validation=str(pilot / "workflow_validation.json"))
        write_json(run_dir / "suite_status.json", state)
        print(json.dumps({"event": "pilot_workflow_passed", **state}, ensure_ascii=False), flush=True)
        launch_child(run_dir)
        wait_child(run_dir, run_dir, "formal_running", state)
        if suite_config.get("recover_infrastructure"):
            from provider_recovery import combined_report

            combined_report(run_dir, final=True)
        else:
            comparison_report(run_dir)
        state.update(status="complete", finished_at_utc=utc_now(), report="comparison_report.md")
        write_json(run_dir / "suite_status.json", state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
    except BaseException as error:
        state.update(status="needs_attention", updated_at_utc=utc_now(), error_type=type(error).__name__, error=str(error))
        write_json(run_dir / "suite_status.json", state)
        print(json.dumps({"event": "retry_suite_stopped", **state}, ensure_ascii=False), flush=True)
        raise


def launch_suite(source: Path, run_dir: Path, pilot: Path, *, source_archived: bool = False, adopt_pilot: Path | None = None, recover_infrastructure: bool = False) -> dict:
    """准备全新目录并启动独立编排进程。Args: source/run_dir/pilot。Returns: 非敏感启动元数据。"""
    from eval_common import ROOT, digest, read_json, utc_now, write_json
    from prepare_dataset import prepare

    if not os.environ.get("VFSSQL_EVAL_EMAIL") or not os.environ.get("VFSSQL_EVAL_PASSWORD"):
        raise ValueError("需设置 VFSSQL_EVAL_EMAIL 与 VFSSQL_EVAL_PASSWORD；凭据不写文件")
    if run_dir.exists() or pilot.exists():
        raise FileExistsError("套件必须使用全新目录，避免复用旧轨迹")
    if recover_infrastructure:
        archived = source.resolve(strict=True)
        if read_json(archived / "pipeline_status.json").get("status") != "complete":
            raise ValueError("服务异常恢复必须基于完整冻结的上一轮")
    elif source_archived:
        archived = source.resolve(strict=True)
        if not archived.is_relative_to((ROOT / "runs/_archive").resolve()) or not (archived / "archival.json").is_file():
            raise ValueError("已有归档不在已验证的评估归档目录")
    else:
        archived = archive_source(source)
    retry_kind = "runtime_error" if recover_infrastructure else "no_retrieval"
    prepare(pilot, pilot=True, retry_source=archived, retry_kind=retry_kind)
    prepared = prepare(run_dir, retry_source=archived, retry_kind=retry_kind)
    config = read_json(run_dir / "capture_config.json")
    if not source_archived and not recover_infrastructure:
        clear_old_selected(source, archived, run_dir)
    write_json(run_dir / "suite_config.json", {"created_at_utc": utc_now(), "source_archive": str(archived), "pilot_run": str(pilot), "formal_run": str(run_dir), "planned_cases": config["planned_cases"], "pilot_cases": 3, "start_formal_only_after_pilot_passed": True, "adopt_pilot": str(adopt_pilot.resolve()) if adopt_pilot else None, "recover_infrastructure": recover_infrastructure}, exclusive=True)
    if recover_infrastructure:
        from provider_recovery import combined_report

        combined_report(run_dir)
        write_json(archived / "infrastructure_correction.json", {"corrected_at_utc": utc_now(), "source_runtime_errors_pending": 168, "additional_original_runtime_errors_pending": 2, "replacement_run": str(run_dir), "old_combined_success_rate_is_final": False, "classification": "runtime_errors_pending_recovery_not_business_failures"})
        (archived / "status_correction.md").write_text("# 运行异常恢复更正\n\n168题因模型服务HTTP502未完成评估，不直接作为业务失败。原保留组另有2题运行异常，一并恢复；原报告及原始轨迹保留审计，原11.73%不作为最终业务成功率。\n\n最新有效评估数、待恢复数及完成后的分组结果见：\n\n" + str(run_dir / "comparison_report.md") + "\n", encoding="utf-8")
    archived_worker = run_dir / "_private/retry_suite_worker.py"
    shutil.copyfile(Path(__file__), archived_worker)
    command = [sys.executable, "-X", "utf8", "-u", "-B", str(archived_worker), "--worker", "--run-dir", str(run_dir)]
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with (run_dir / "suite.log").open("ab") as stdout, (run_dir / "suite_error.log").open("ab") as stderr:
        kwargs = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, close_fds=True, env=os.environ.copy())
        try:
            process = subprocess.Popen(command, creationflags=flags | (subprocess.CREATE_BREAKAWAY_FROM_JOB if os.name == "nt" else 0), **kwargs)
        except OSError as error:
            if os.name != "nt" or error.winerror != 5:
                raise
            process = subprocess.Popen(command, creationflags=flags, **kwargs)
    result = {**prepared, "pid": process.pid, "started_at_utc": utc_now(), "pilot_run": str(pilot), "source_archive": str(archived), "worker_sha256": digest(archived_worker.read_bytes()), "status_file": "suite_status.json", "log": "suite.log"}
    write_json(run_dir / "suite_launcher.json", result)
    return result


def main() -> None:
    """命令行启动或后台工作入口。Args: 命令行参数。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--pilot-dir", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source-archived", action="store_true")
    parser.add_argument("--adopt-pilot", type=Path)
    parser.add_argument("--recover-infrastructure", action="store_true")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    if args.worker:
        sys.path.insert(0, str(run_dir / "_private/frozen_evaluator"))
        worker(run_dir)
    else:
        if not args.source_run or not args.pilot_dir:
            parser.error("启动套件需要 --source-run 与 --pilot-dir")
        print(json.dumps(launch_suite(args.source_run, run_dir, args.pilot_dir.resolve(), source_archived=args.source_archived, adopt_pilot=args.adopt_pilot, recover_infrastructure=args.recover_infrastructure), ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
