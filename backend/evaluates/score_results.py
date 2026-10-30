"""冻结全部预测后离线审计、评分、统计检索轮数并生成报告。"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from uuid import uuid4

from deerflow_evidence import audit_prediction
from eval_common import EXPLICIT_MODE, NATIVE_MODES, RETRIEVAL_TOOLS, ROOT, check_frozen, code_hashes, digest, read_json, utc_now, write_json
from schema_metrics import retrieval_metrics, score_prediction

SEARCH_TOOLS = {"vfs_find_file", "vfs_grep_file", "vfs_grep", "search_sql_schema"}
READ_TOOLS = {"vfs_cat_read", "vfs_sed", "vfs_bash_execute", "inspect_sql_schema"}


def retrieval_rounds(sample: dict, calls: list[dict], name_map: dict) -> dict:
    """按实际返回顺序重放首次完整覆盖。Args: sample/calls/name_map。Returns: 轮数。"""
    rounds, retrieval_calls, reads = 0, 0, 0
    result = {"first_complete_search_round": None, "first_complete_retrieval_call": None, "first_complete_mcp_call": None}
    for index, call in enumerate(calls, start=1):
        tool = call["tool"]
        rounds += tool in SEARCH_TOOLS
        retrieval_calls += tool in SEARCH_TOOLS | READ_TOOLS
        reads += tool in READ_TOOLS and (tool != "vfs_sed" or call["arguments"].get("operation") == "print_range")
        if result["first_complete_mcp_call"] is None and retrieval_metrics(sample, calls[:index], name_map)["structure_complete"]:
            result.update(first_complete_search_round=rounds, first_complete_retrieval_call=retrieval_calls, first_complete_mcp_call=index)
    return {"retrieval_rounds_total": rounds, "structure_read_calls": reads, **result}


def score_one(sample: dict, record: dict, budgets: dict, *, audit_ok: bool, name_map: dict | None = None) -> dict:
    """主指标保持用户表字段命中＋执行口径。Args: 样本/预测/预算/审计。Returns: 指标。"""
    name_map = name_map or {}
    result = score_prediction(sample, record, name_map, budgets)
    protocol = not record.get("protocol_issues") and not record.get("stop_reason") and result["protocol_compliant"]
    capture_ok = audit_ok and record["capture"]["complete"]
    primary = bool(result["structure_complete"] and result["execution_ok"] and record["status"] == "completed" and record.get("run_status") == "success" and protocol and capture_ok)
    strict = primary and result["sql_structure_complete"]
    result.update(
        primary_success=primary,
        strict_sql_success=strict,
        retrieval_used=any(call["tool"] in RETRIEVAL_TOOLS for call in record["tool_calls"]),
        skill_activation_verified=bool(record.get("skill_activation", {}).get("verified")),
        trace_audit_ok=audit_ok,
        protocol_compliant=protocol,
        capture_complete=capture_ok,
        run_status=record.get("run_status"),
        protocol_issues=record.get("protocol_issues", []),
        capture_issues=record["capture"]["issues"],
        all_tool_calls=len(record.get("all_tool_calls", [])),
        subagent_mcp_calls=sum(bool(c["scope"]) for c in record["tool_calls"]),
        capture_warnings=record["capture"].get("warnings", []),
        answer_present=bool(record.get("answer_text", "").strip()),
        sql_source=record.get("sql_source", "model_final_json"),
        timing_source=record.get("timing_source", "host_monotonic_observation"),
        observed_elapsed_seconds=record.get("observed_elapsed_seconds", record["elapsed_seconds"]),
        tool_observation_threshold_exceeded=len(record["tool_calls"]) > budgets["max_tool_calls"],
        sql_observation_threshold_exceeded=sum(c["tool"] == "sql_execute" for c in record["tool_calls"]) > budgets["max_sql_executions"],
    )
    result.update(retrieval_rounds(sample, record["tool_calls"], name_map))
    return result


def summarize(scores: list[dict], cases: list[dict]) -> dict:
    """按固定题数和 family 汇总。Args: scores/cases。Returns: 汇总。"""
    total = len(cases)
    elapsed = sorted(s["elapsed_seconds"] for s in scores if s.get("elapsed_seconds") is not None)
    families: dict[str, list[bool]] = {}
    by_id = {s["case_id"]: s for s in scores}
    for case in cases:
        families.setdefault(case["family_id"], []).append(by_id[case["case_id"]]["primary_success"])
    complete = [s for s in scores if s["first_complete_mcp_call"] is not None]
    return {
        "total": total,
        "families": len(families),
        "primary_success": sum(s["primary_success"] for s in scores),
        "primary_success_rate": sum(s["primary_success"] for s in scores) / total,
        "strict_sql_success": sum(s["strict_sql_success"] for s in scores),
        "strict_sql_success_rate": sum(s["strict_sql_success"] for s in scores) / total,
        "execution_success": sum(s["execution_ok"] for s in scores),
        "execution_success_rate": sum(s["execution_ok"] for s in scores) / total,
        "retrieval_used_cases": sum(s.get("retrieval_used", False) for s in scores),
        "skill_activation_verified_cases": sum(s.get("skill_activation_verified", False) for s in scores),
        "structure_complete": sum(s["structure_complete"] for s in scores),
        "family_macro_primary_success_rate": statistics.mean(statistics.mean(values) for values in families.values()),
        "first_complete_search_round_distribution": dict(sorted(Counter(str(s["first_complete_search_round"]) for s in complete).items())),
        "never_complete_retrieval": total - len(complete),
        "retrieval_rounds_total": sum(s["retrieval_rounds_total"] for s in scores),
        "retrieval_rounds_mean": sum(s["retrieval_rounds_total"] for s in scores) / total,
        "retrieval_rounds_distribution": dict(sorted(Counter(str(s["retrieval_rounds_total"]) for s in scores).items())),
        "first_complete_retrieval_call_distribution": dict(sorted(Counter(str(s["first_complete_retrieval_call"]) for s in complete).items())),
        "first_complete_mcp_call_distribution": dict(sorted(Counter(str(s["first_complete_mcp_call"]) for s in complete).items())),
        "mcp_calls": sum(s["tool_calls"] for s in scores),
        "sql_calls": sum(s["sql_executions"] for s in scores),
        "subagent_mcp_calls": sum(s["subagent_mcp_calls"] for s in scores),
        "capture_complete": sum(s["capture_complete"] for s in scores),
        "trace_audit_ok": sum(s["trace_audit_ok"] for s in scores),
        "protocol_compliant": sum(s["protocol_compliant"] for s in scores),
        "latency_p50_seconds": statistics.median(elapsed) if elapsed else None,
        "latency_p95_seconds": elapsed[math.ceil(len(elapsed) * 0.95) - 1] if elapsed else None,
        "total_case_seconds": sum(elapsed),
        "reason_counts": dict(Counter(reason for s in scores for reason in s["reasons"])),
        "protocol_issue_counts": dict(Counter(reason for s in scores for reason in s["protocol_issues"])),
        "capture_issue_counts": dict(Counter(reason for s in scores for reason in s["capture_issues"])),
        "capture_warning_counts": dict(Counter(reason for s in scores for reason in s.get("capture_warnings", []))),
        "answers_present": sum(s.get("answer_present", False) for s in scores),
        "tool_observation_threshold_exceeded": sum(s.get("tool_observation_threshold_exceeded", False) for s in scores),
        "sql_observation_threshold_exceeded": sum(s.get("sql_observation_threshold_exceeded", False) for s in scores),
        "mean_table_recall": statistics.mean(s["table_recall"] for s in scores),
        "mean_column_coverage": statistics.mean(s["column_coverage"] for s in scores),
    }


def report(config: dict, summary: dict, scores: list[dict], snapshot: str) -> str:
    """生成人类可读报告。Args: config/summary/scores/snapshot。Returns: Markdown。"""
    s = summary
    native = config.get("evaluation_mode") in NATIVE_MODES
    explicit = config.get("evaluation_mode") == EXPLICIT_MODE
    input_description = "每题一个新 thread；并发固定 1；输入为 /vfssql-retrieval、必须使用 vfssql-retrieval skill。及样本原问题；宿主原生注入 Skill 正文。" if explicit else "每题一个新 thread；并发固定 1；仅发送原始问题，由 DeerFlow 自主发现原生 Skill；最终答复按自然语言原文归档。"
    budget_description = (
        "等待模型自然结束，不设执行时间上限，记录每题耗时；" if native else f"每题 {config['budgets']['timeout_seconds']} 秒超时；"
    ) + f"{config['budgets']['max_tool_calls']} 次 VFSSQL MCP / {config['budgets']['max_sql_executions']} 次 SQL"
    budget_description += "为观察阈值，超出只记录，不取消对话；主/子代理、失败/重试/枚举合计。" if native else "为硬上限；主/子代理、失败/重试/枚举合计。"
    lines = [
        "# DeerFlow VFSSQL 独立对话评估报告",
        "",
        f"生成于 {utc_now()}；用途：{config['purpose']}；评分快照：`{snapshot}`。",
        "",
        "## 1、运行条件",
        "",
        f"- 模型：**{config['context']['model_name']}**；Ultra，推理深度 high。",
        "- " + input_description if native else "- 每题一个新 thread；专用账号；Skill 文本显式注入；没有跨题查询计划复用。",
        f"- 预算：{budget_description}",
        f"- 数据集 SHA-256：`{config['dataset_sha256']}`。",
        f"- Skill SHA-256：`{config['skill_sha256']}`。",
        f"- 仓库提交：`{config['repository_revision']}`；服务版本/索引快照未知项保留 null。",
        "",
        "## 2、结果",
        "",
        "主成功要求：实际检索完整覆盖目标表及按表绑定的字段，最终同一 SQL/参数真实执行成功；允许空结果。严格 SQL 字段引用另计。",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 检索完整＋最终执行成功（用户口径） | {s['primary_success']}/{s['total']}，{s['primary_success_rate']:.2%} |",
        f"| 最终 SQL 也完整引用目标表字段 | {s['strict_sql_success']}/{s['total']}，{s['strict_sql_success_rate']:.2%} |",
        f"| 最终 SQL 实际执行成功 | {s['execution_success']}/{s['total']}，{s['execution_success_rate']:.2%} |",
        f"| 检索结构完整 | {s['structure_complete']}/{s['total']} |",
        f"| 实际使用定位或内容读取工具 | {s['retrieval_used_cases']}/{s['total']} |",
        f"| 原生 Skill 正文激活已核验 | {s['skill_activation_verified_cases']}/{s['total']} |",
        f"| Family 宏平均主成功率 | {s['family_macro_primary_success_rate']:.2%} |",
        f"| 原始轨迹审计通过 | {s['trace_audit_ok']}/{s['total']} |",
        f"| 采集完整 | {s['capture_complete']}/{s['total']} |",
        f"| 有自然语言最终答复 | {s['answers_present']}/{s['total']} |" if native else "| 输出协议 | 旧版最终 JSON |",
        f"| 超出 MCP / SQL 观察阈值的题数 | {s['tool_observation_threshold_exceeded']} / {s['sql_observation_threshold_exceeded']} |",
        f"| MCP / SQL 次数 | {s['mcp_calls']} / {s['sql_calls']} |",
        f"| 子代理 MCP 次数 | {s['subagent_mcp_calls']} |",
        f"| 每题耗时 p50 / p95（秒） | {s['latency_p50_seconds']:.1f} / {s['latency_p95_seconds']:.1f} |",
        "",
        "## 3、多少轮检索完成",
        "",
        "一次 find/grep 请求及其返回记一轮定位或补查；结构读取另计，初始化/SQL 不计定位轮数。首次完整轮数按真实返回顺序离线重放；未完整覆盖保持 null。",
        "",
        "| 首次完整覆盖时的定位轮数 | 题数 |",
        "| --- | ---: |",
    ]
    lines += [f"| 第 {key} 轮 | {value} |" for key, value in s["first_complete_search_round_distribution"].items()]
    lines += [
        f"| 结束时仍未完整 | {s['never_complete_retrieval']} |",
        "",
        f"全部题累计 **{s['retrieval_rounds_total']} 轮**，平均 **{s['retrieval_rounds_mean']:.2f} 轮/题**。逐题 `scores.json` 保留首次完整轮数、内容检索调用序号、MCP 序号和结构读取次数。",
        "",
        "## 4、证据与限制",
        "",
        "- 原始 SSE、分页 API 正文、monotonic/UTC 收据、原生消息/工具 ID 和文件哈希均保存在 traces/。评分重放原始证据并比较冻结预测。",
        "- 每题耗时优先采用 Gateway run.created_at 至 updated_at 的终态运行时长，另记宿主观察时长；服务时间缺失时才回退至宿主 monotonic。工具耗时是宿主观察的接收间隔，不能当作独立服务端调用延迟。",
        "- Ultra 原生子图证据与步骤摘要分开处理，摘要截断不补造调用 ID；证据缺失单列。工具原始返回与模型收到的截断/摘要存在差异时保留实际原文。",
        "- 题库/目标标签只在全部预测冻结后用于评分。没有用评分反馈指导本轮后续问题。",
        "- /vfssql-retrieval 使用 DeerFlow 原生激活机制注入 Skill 正文，并通过 middleware:skill_activation 的名称及内容哈希核验。没有定位或内容读取的题不计 MCP 检索成功；只列目录属于初始化。"
        if explicit else
        "- 原生 Skill 从 skills/public/z-vfssql-retrieval 自动发现；快照只用于审计，不注入消息。SQL/参数来源标注为最后一次 sql_execute 请求；要求有最终答复和成功运行，执行空结果允许通过。"
        if native
        else "- 旧 Skill 含已删除的结构化工具名，提示词沿用按实际工具 schema 调用的规则；本轮未替换 Skill。",
        "- 不因耗时、MCP 或 SQL 次数取消模型运行；活动会话遇到控制接口故障时持续重连等待，不创建下一题。模型自然结束后记录其最终输出或错误；原始工具请求/返回缺失仍单列失败。",
        "- 业务数据与 Gold 结果等价性不在本轮成功口径内。",
        "",
        "## 5、与上一轮的对照",
        "",
        "上轮 Codex Desktop、GPT-6.1-sol/medium、共享上下文及部分查询结构复用：248/324（76.54%）。本轮更改模型、宿主、上下文方式。"
        "本轮显式 Skill 复测选择先前未使用定位或内容读取工具的子集，不能与全量统一提示实验等同；原轮与复测分组汇总。" if explicit else "原生轮还取消 Skill/评估提示注入，并将调用次数从硬上限改为观察阈值；分差反映整体运行条件差异，不能只归因于模型。",
        "",
        "## 6、原因码",
        "",
        "```json",
        json.dumps({"scoring": s["reason_counts"], "protocol": s["protocol_issue_counts"], "capture": s["capture_issue_counts"], "transport_warnings": s["capture_warning_counts"]}, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def score(run_dir: Path) -> dict:
    """审计全部冻结预测并保存版本化评分。Args: run_dir。Returns: 汇总。"""
    config = check_frozen(run_dir)
    frozen = read_json(run_dir / "predictions_frozen.json")
    manifest = read_json(run_dir / "_private/manifest.json")
    samples = read_json(ROOT / "datasets/vfssql-evaluation-dataset.v1.json")
    scores, records, audit_hashes = [], [], {}
    if set(frozen["prediction_hashes"]) != {c["case_id"] for c in manifest["cases"]}:
        raise ValueError("冻结题目集合不完整")
    for case in manifest["cases"]:
        path = run_dir / "predictions" / f"{case['case_id']}.json"
        if digest(path.read_bytes()) != frozen["prediction_hashes"][case["case_id"]]:
            raise ValueError("预测冻结后发生变化")
        record = read_json(path)
        sample = samples[case["dataset_index"]]
        audited = audit_prediction(run_dir, record)
        scores.append(score_one(sample, record, config["budgets"], audit_ok=audited))
        records.append(record)
        audit_hashes[case["case_id"]] = record["capture"]["files"]
    summary = summarize(scores, manifest["cases"])
    summary.update(
        scorer_version="deerflow-2.0-native+vfssql-2.0",
        code_hashes=code_hashes(),
        purpose=config["purpose"],
        evaluation_mode=config.get("evaluation_mode"),
        concurrency=config.get("concurrency"),
        complete=True,
        scored_at_utc=utc_now(),
        tool_counts=dict(Counter(c["tool"] for r in records for c in r["tool_calls"])),
        run_status_counts=dict(Counter(r["run_status"] for r in records)),
        stop_reason_counts=dict(Counter(r["stop_reason"] for r in records if r.get("stop_reason"))),
    )
    if config.get("provider_recovery"):
        summary.update(infrastructure_errors_excluded=True, infrastructure_error_attempts=len(list((run_dir / "infra_attempts").glob("*/*/metadata.json"))), pending_runtime_recovery_cases=0)
    stamp = datetime_stamp = summary["scored_at_utc"].replace(":", "").replace("-", "").split(".")[0] + "-" + uuid4().hex[:8]
    snapshot = run_dir / "scoring" / stamp
    snapshot.mkdir(exist_ok=False)
    write_json(snapshot / "scores.json", scores, exclusive=True)
    write_json(snapshot / "summary.json", summary, exclusive=True)
    write_json(snapshot / "trace_hashes.json", audit_hashes, exclusive=True)
    write_json(snapshot / "prediction_hashes.json", frozen["prediction_hashes"], exclusive=True)
    text = report(config, summary, scores, datetime_stamp)
    (snapshot / "report.md").write_text(text, encoding="utf-8", newline="\n")
    write_json(run_dir / "summary.json", summary)
    (run_dir / "report.md").write_text(text, encoding="utf-8", newline="\n")
    return {key: summary[key] for key in ["total", "primary_success", "primary_success_rate", "strict_sql_success", "execution_success", "capture_complete", "retrieval_rounds_total", "never_complete_retrieval"]}


def main() -> None:
    """离线评分命令入口。Args: 命令行参数。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(score(args.run_dir.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
