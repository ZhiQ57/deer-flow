"""将模型服务故障保留为待恢复尝试，串行等待同题恢复后才评估下一题。"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from deerflow_evidence import audit_prediction, recorded_chunks
from eval_common import append_json, check_frozen, digest, read_json, utc_now, write_json
from run_deerflow import GatewayFailure, run


def model_errors(trace: Path) -> list[dict]:
    """读取去重的原生模型错误事件。Args: trace。Returns: 事件序号及错误文本。"""
    events = {}
    for raw, receipt in recorded_chunks(trace, "http"):
        if receipt.get("status_code") != 200 or not receipt.get("path", "").endswith("/events"):
            continue
        for event in json.loads(raw):
            if event.get("event_type") == "llm.error":
                events[event["seq"]] = {"event_seq": event["seq"], "message": str(event.get("content", ""))}
    return list(events.values())


def infrastructure_failure(record: dict, trace: Path) -> dict | None:
    """区分模型基础设施异常与已完成的业务失败。Args: record/trace。Returns: 异常分类或无。"""
    if record["run_status"] == "success":
        return None
    errors = model_errors(trace)
    text = "\n".join(event["message"] for event in errors)
    answer = record.get("answer_text", "")
    transient = bool(re.search(r"\b(?:429|5\d\d)\b|timeout|timed out|connect(?:ion|error)|temporarily unavailable", text, flags=re.IGNORECASE))
    fallback = answer.startswith("The configured LLM provider is temporarily unavailable after multiple retries.")
    return {"classification": "model_provider_unavailable" if transient or fallback else "unclassified_runtime_error", "retryable": record["run_status"] == "error" and (transient or fallback), "model_errors": errors, "run_status": record["run_status"], "business_failure": False}


def archive_attempt(run_dir: Path, record: dict, reason: dict) -> Path:
    """归档自然结束的基础设施异常，移出业务评分候选。Args: run_dir/record/reason。Returns: 尝试目录。"""
    case_id = record["case_id"]
    if not re.fullmatch(r"case-\d{4}", case_id) or (run_dir / "predictions_frozen.json").exists():
        raise ValueError("题号非法或业务预测已冻结，拒绝移动证据")
    trace = (run_dir / "traces" / case_id).resolve()
    prediction = (run_dir / "predictions" / f"{case_id}.json").resolve()
    if not trace.is_relative_to(run_dir / "traces") or not prediction.is_relative_to(run_dir / "predictions"):
        raise ValueError("尝试归档源路径越界")
    if not read_json(trace / "terminal.json").get("terminal_confirmed") or not audit_prediction(run_dir, record):
        raise RuntimeError("异常尝试终态或原始证据未核验，保持待处理，不重发当前模型任务")
    parent = run_dir / "infra_attempts" / case_id
    previous = [int(path.name.removeprefix("try-")) for path in parent.glob("try-*")]
    target = (parent / f"try-{max(previous, default=0) + 1:04d}").resolve()
    if not target.is_relative_to((run_dir / "infra_attempts").resolve()):
        raise ValueError("尝试归档目标越界")
    (target / "traces").mkdir(parents=True, exist_ok=False)
    (target / "predictions").mkdir()
    shutil.copyfile(run_dir / "capture_config.json", target / "capture_config.json")
    prediction_sha = digest(prediction.read_bytes())
    # 路径全部验证位于当前恢复批次内；自然终态后移动，不取消模型、不覆盖历史尝试。
    trace.rename(target / "traces" / case_id)
    prediction.rename(target / "predictions" / f"{case_id}.json")
    if not audit_prediction(target, record):
        raise RuntimeError("移动后的异常证据无法重放，停止提交新问题")
    metadata = {"archived_at_utc": utc_now(), "case_id": case_id, "thread_id": record["thread_id"], "run_id": record["run_id"], "elapsed_seconds": record["elapsed_seconds"], "mcp_calls": len(record["tool_calls"]), "prediction_sha256": prediction_sha, **reason}
    write_json(target / "metadata.json", metadata, exclusive=True)
    append_json(run_dir / "infrastructure_attempts.jsonl", {**metadata, "attempt_path": str(target.relative_to(run_dir))})
    return target


async def wait_for_provider(run_dir: Path, case_id: str, failures: int, valid: int, reason: str, config: dict) -> None:
    """按退避间隔等待服务恢复并每30秒输出心跳。Args: 批次/题号/次数/有效数/原因/配置。Returns: 无。"""
    schedule = config["provider_backoff_seconds"]
    seconds = schedule[min(failures - 1, len(schedule) - 1)]
    deadline = time.monotonic() + seconds
    retry_at = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
    state = {"status": "waiting_for_provider", "case_id": case_id, "valid_predictions": valid, "planned_cases": config["planned_cases"], "consecutive_failures_current_case": failures, "reason": reason, "next_retry_at_utc": retry_at, "wait_seconds": seconds, "business_failure": False}
    while True:
        remaining = max(0, deadline - time.monotonic())
        state.update(updated_at_utc=utc_now(), remaining_wait_seconds=round(remaining, 1))
        write_json(run_dir / "recovery_status.json", state)
        print(json.dumps({"event": "provider_recovery_heartbeat", **state}, ensure_ascii=False), flush=True)
        if remaining <= 0:
            return
        await asyncio.sleep(min(30, remaining))


async def recover(run_dir: Path) -> None:
    """只将正常完成的模型尝试交给业务评分。Args: run_dir。Returns: 无。"""
    config = check_frozen(run_dir)
    if not config.get("provider_recovery") or not config.get("defer_prediction_freeze"):
        raise ValueError("恢复批次缺少显式授权的基础设施重试配置")
    questions = [json.loads(line) for line in (run_dir / "questions.jsonl").read_text(encoding="utf-8").splitlines()]
    valid = 0
    for question in questions:
        case_id = question["case_id"]
        prediction = run_dir / "predictions" / f"{case_id}.json"
        failures = len(list((run_dir / "infra_attempts" / case_id).glob("try-*/metadata.json")))
        while True:
            check_frozen(run_dir)
            if not prediction.exists():
                write_json(run_dir / "recovery_status.json", {"status": "running_current_case", "case_id": case_id, "valid_predictions": valid, "planned_cases": len(questions), "updated_at_utc": utc_now()})
                try:
                    # 每次仅受理当前一个问题。新尝试为独立对话，题面保持完全一致。
                    await run(run_dir, 1)
                except GatewayFailure as error:
                    if str(error) == "检测到知识学习调用，已停止批次":
                        raise
                    if not re.search(r"HTTP (?:429|5\d\d)|ConnectError|ReadTimeout|ConnectTimeout|RemoteProtocolError", str(error)):
                        raise
                    failures += 1
                    append_json(run_dir / "provider_transport_events.jsonl", {"at_utc": utc_now(), "case_id": case_id, "reason": str(error), "business_failure": False})
                    await wait_for_provider(run_dir, case_id, failures, valid, "gateway_transport_unavailable", config)
                    continue
            if not prediction.exists():
                raise RuntimeError("当前题未生成可核验终态，保持待处理，不开始后续题")
            record = read_json(prediction)
            failure = infrastructure_failure(record, run_dir / "traces" / case_id)
            if failure is None:
                valid += 1
                append_json(run_dir / "evaluated_cases.jsonl", {"case_id": case_id, "thread_id": record["thread_id"], "run_id": record["run_id"], "accepted_at_utc": utc_now(), "infrastructure_retries": failures, "elapsed_seconds": record["elapsed_seconds"], "business_verdict": "pending_offline_scoring"})
                print(json.dumps({"event": "case_ready_for_business_scoring", "case_id": case_id, "valid_predictions": valid, "planned_cases": len(questions)}, ensure_ascii=False), flush=True)
                if config["purpose"] == "formal":
                    combined_report(run_dir)
                break
            if not failure["retryable"]:
                write_json(run_dir / "recovery_status.json", {"status": "needs_attention", "case_id": case_id, "valid_predictions": valid, "business_failure": False, **failure})
                raise RuntimeError("当前题为未分类运行异常，保持待评估，不计业务失败或继续后续问题")
            if any(call["tool"] == "sql_learn" for call in record["tool_calls"]):
                from supervise_evaluation import learning_was_rejected

                if not learning_was_rejected(record):
                    raise RuntimeError("异常尝试含未确认拒绝的知识调用，保留现场，不自动重复")
            archived = archive_attempt(run_dir, record, failure)
            failures += 1
            print(json.dumps({"event": "infrastructure_attempt_archived", "case_id": case_id, "attempt_path": str(archived.relative_to(run_dir)), "business_failure": False}, ensure_ascii=False), flush=True)
            await wait_for_provider(run_dir, case_id, failures, valid, failure["classification"], config)
    hashes = {question["case_id"]: digest((run_dir / "predictions" / f"{question['case_id']}.json").read_bytes()) for question in questions}
    frozen = run_dir / "predictions_frozen.json"
    if frozen.exists():
        if read_json(frozen)["prediction_hashes"] != hashes:
            raise ValueError("有效预测冻结哈希不一致")
    else:
        write_json(frozen, {"frozen_at_utc": utc_now(), "planned_cases": len(questions), "prediction_hashes": hashes, "infrastructure_errors_excluded": True}, exclusive=True)
    write_json(run_dir / "recovery_status.json", {"status": "ready_for_scoring", "valid_predictions": valid, "planned_cases": len(questions), "pending_infrastructure_cases": 0, "updated_at_utc": utc_now()})


def combined_report(run_dir: Path, *, final: bool = False) -> None:
    """合并有效旧记录与恢复结果，明确待评估题不属于业务失败。Args: run_dir/final。Returns: 无。"""
    config = read_json(run_dir / "capture_config.json")
    source = Path(config["retry_source"])
    selection = read_json(run_dir / "_private/retry_selection.json")
    selected_ids = {case["case_id"] for case in selection["selected_cases"]}
    previous_selection = read_json(source / "_private/retry_selection.json")
    original = Path(previous_selection["source_run"])
    old_scores = {item["case_id"]: item for item in read_json(sorted((source / "scoring").glob("*/scores.json"))[-1])}
    original_scores = {item["case_id"]: item for item in read_json(sorted((original / "scoring").glob("*/scores.json"))[-1])}
    old_valid = [old_scores[case["case_id"]] for case in selection["retained_cases"]]
    original_valid = [original_scores[case["case_id"]] for case in previous_selection["retained_cases"] if case["case_id"] not in selected_ids]
    baseline = old_valid + original_valid
    if len(baseline) + len(selected_ids) != 324 or any(item["run_status"] != "success" for item in baseline):
        raise ValueError("有效旧记录、待恢复集合或全量题数不一致")
    valid_records = [read_json(path) for path in (run_dir / "predictions").glob("case-*.json")]
    accepted = [record for record in valid_records if record["run_status"] == "success"]
    baseline_success = sum(item["primary_success"] for item in baseline)
    summary = {"updated_at_utc": utc_now(), "status": "complete" if final else "recovery_in_progress", "total_dataset_cases": 324, "recovery_cases": len(selected_ids), "previous_502_cases": 168, "additional_original_runtime_errors": len(selection["extra_original_runtime_cases"]), "retained_valid_scored_cases": len(baseline), "retained_primary_success": baseline_success, "retained_business_failures": len(baseline) - baseline_success, "new_valid_predictions": len(accepted), "pending_runtime_recovery_cases": len(selected_ids) - len(accepted), "new_valid_pending_scoring_cases": 0 if final else len(accepted), "infrastructure_error_attempts": len(list((run_dir / "infra_attempts").glob("*/*/metadata.json"))), "runtime_errors_counted_as_business_failure": False, "combined_primary_success": None, "combined_primary_success_rate": None, "note": "恢复尚未完成时不发布全量最终业务成功率；所有服务错误尝试保留，正常完成但检索或SQL不合格的业务失败不重做择优。"}
    lines = ["# 模型服务异常恢复评估", "", f"更新时间：{summary['updated_at_utc']}。", "", "## 当前分组", "", "| 分组 | 题数 | 状态 |", "| --- | ---: | --- |", f"| 已有有效并评分记录 | {len(baseline)} | 成功{baseline_success}，业务失败{len(baseline)-baseline_success} |", f"| 本轮正常完成的新预测 | {len(accepted)} | {'已评分' if final else '待本轮统一冻结后评分'} |", f"| 尚待恢复 | {summary['pending_runtime_recovery_cases']} | 不计业务失败 |", "", "重测范围为上轮168道模型HTTP502异常题，加原保留组2道运行异常题，共170题。", ""]
    if final:
        new_scores = read_json(sorted((run_dir / "scoring").glob("*/scores.json"))[-1])
        if len(accepted) != len(selected_ids) or len(new_scores) != len(selected_ids):
            raise ValueError("恢复结果不完整，拒绝发布最终业务成功率")
        success = baseline_success + sum(item["primary_success"] for item in new_scores)
        summary.update(combined_primary_success=success, combined_primary_success_rate=success/324, new_primary_success=sum(item["primary_success"] for item in new_scores), final_business_failures=324-success)
        lines.extend(["## 最终业务结果", "", f"- 真实检索完整命中并最终SQL执行成功：{success}/324（{success/324:.2%}）。", f"- 本轮170题成功：{summary['new_primary_success']}/170。", f"- 新基础设施错误尝试：{summary['infrastructure_error_attempts']}次，保留为独立尝试，不进入业务失败数。", ""])
    else:
        lines.extend(["当前不把旧168道HTTP502错误或原2道未完成运行归入业务失败。原11.73%仅是旧流水线机械统计，不能作为本次修正后的最终业务结论。", ""])
    lines.extend(["## 运行与证据", "", "- 一题一个独立对话，显式/vfssql-retrieval及固定一句提示；Qwen3.8-27B-FP8-Local-cloud，Ultra/high，并发1。", "- 先三题验证，再自动正式恢复；模型自然结束前不开始下一题，不因耗时或调用数中断。", "- 模型服务异常自然结束后归档到infra_attempts，当前题按60/120/300/600秒退避，暂停新题，每30秒心跳。", "- 新有效预测统一冻结后评分；正常完成的业务失败不重复抽取更好结果。", "- report.md为本轮独立评分，comparison_summary.json为分组汇总，recovery_status.json为当前等待/运行状态。", ""])
    write_json(run_dir / "comparison_summary.json", summary)
    (run_dir / "comparison_report.md").write_text("\n".join(lines), encoding="utf-8")
