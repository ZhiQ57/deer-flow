"""只读冻结评分与原始预测，生成用户指定的四组对照指标。"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from uuid import uuid4

from eval_common import digest, read_json, utc_now, write_json

CODEX_RUN = Path(r"D:\A-AICodeWork\ownmindforge\backend\evaluate\vfssql-evaluate\runs\20261021-gpt61sol-medium-desktop")


def mean(values: list[float]) -> float | None:
    """计算空集合可识别的均值。Args: values。Returns: 均值或无。"""
    return statistics.mean(values) if values else None


def f1(precision: float, recall: float) -> float:
    """计算单题表集合F1。Args: precision/recall。Returns: F1。"""
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def duration_summary(values: list[float]) -> dict:
    """汇总保存的耗时，P95采用最近秩。Args: values。Returns: 样本数、均值和分位数。"""
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("计时样本必须是有限非负数")
    ordered = sorted(values)
    return {"n": len(values), "mean_seconds": mean(values), "p50_seconds": statistics.median(values) if values else None, "p95_seconds": ordered[math.ceil(0.95 * len(values)) - 1] if values else None, "sum_seconds": sum(values)}


def summarize_rows(rows: list[dict]) -> dict:
    """逐题计算后宏平均，保留全部有效样本分母。Args: rows。Returns: 四组指标及轮数。"""
    if not rows or len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("指标集合为空或存在重复题")
    n = len(rows)
    successes = [r for r in rows if r["primary_success"]]
    count_keys = ["candidate_complete", "structure_complete", "first_execution_ok", "execution_ok", "primary_success", "strict_sql_success"]
    counts = {key: sum(bool(r[key]) for r in rows) for key in count_keys}
    complete = [r for r in rows if r.get("first_complete_mcp_call") is not None]
    return {
        "n": n,
        "discovery": {
            "first_recall_at_k": {str(k): mean([r["first_discovery_recall_at_k"][str(k)] for r in rows]) for k in (1, 3, 5, 10)},
            "candidate_table_recall": mean([r["candidate_table_recall"] for r in rows]),
            "candidate_table_precision": mean([r["candidate_table_precision"] for r in rows]),
            "candidate_table_macro_f1": mean([f1(r["candidate_table_precision"], r["candidate_table_recall"]) for r in rows]),
            "candidate_complete_rate": counts["candidate_complete"] / n,
        },
        "structure": {
            "table_recall": mean([r["table_recall"] for r in rows]),
            "table_precision": mean([r["retrieved_table_precision"] for r in rows]),
            "table_macro_f1": mean([f1(r["retrieved_table_precision"], r["table_recall"]) for r in rows]),
            "column_recall": mean([r["column_coverage"] for r in rows]),
            "complete_rate": counts["structure_complete"] / n,
        },
        "task": {
            "first_sql_error_free_rate": counts["first_execution_ok"] / n,
            "final_sql_error_free_rate": counts["execution_ok"] / n,
            "primary_success_rate": counts["primary_success"] / n,
            "strict_sql_joint_rate": counts["strict_sql_success"] / n,
            "counts": counts,
        },
        "efficiency": {
            "saved_elapsed": duration_summary([r["elapsed_seconds"] for r in rows]),
            "mean_mcp_calls": mean([r["tool_calls"] for r in rows]),
            "total_mcp_calls": sum(r["tool_calls"] for r in rows),
            "successful_saved_elapsed": duration_summary([r["elapsed_seconds"] for r in successes]),
            "successful_mean_mcp_calls": mean([r["tool_calls"] for r in successes]),
            "successful_n": len(successes),
            "host_observed_elapsed": duration_summary([r["observed_elapsed_seconds"] for r in rows if r.get("observed_elapsed_seconds") is not None]),
            "capture_elapsed": duration_summary([r["capture_elapsed_seconds"] for r in rows if r.get("capture_elapsed_seconds") is not None]),
            "timing_source_counts": dict(Counter(r["timing_source"] for r in rows)),
        },
        "rounds": {
            "total_search_rounds": sum(r["retrieval_rounds_total"] for r in rows),
            "mean_search_rounds": mean([r["retrieval_rounds_total"] for r in rows]),
            "successful_mean_search_rounds": mean([r["retrieval_rounds_total"] for r in successes]),
            "search_round_distribution": dict(sorted(Counter(str(r["retrieval_rounds_total"]) for r in rows).items())),
            "first_complete_search_round_distribution": dict(sorted(Counter(str(r["first_complete_search_round"]) for r in complete).items())),
            "never_complete": n - len(complete),
            "structure_complete_cases": len(complete),
        },
        "prompt_group_counts": dict(Counter(r["prompt_group"] for r in rows)),
        "source_run_counts": dict(Counter(r["source_run"] for r in rows)),
    }


def score_path(run_dir: Path) -> Path:
    """定位已有评分明确指向的快照。Args: run_dir。Returns: 评分文件。"""
    summary = read_json(run_dir / "summary.json")
    explicit = summary.get("scoring_snapshot")
    if explicit:
        return run_dir / explicit / "scores.json"
    stamp = summary["scored_at_utc"]
    matches = [path for path in (run_dir / "scoring").glob("*/scores.json") if read_json(path.parent / "summary.json").get("scored_at_utc") == stamp]
    if len(matches) != 1:
        raise ValueError("无法唯一定位本轮已发布评分，拒绝任取评分结果")
    return matches[0]


def load_rows(run_dir: Path, case_ids: set[str] | None = None, *, codex: bool = False) -> tuple[list[dict], dict]:
    """读取冻结预测与评分，核对哈希并只输出无题面指标。Args: 目录/题号/codex。Returns: 逐题指标和来源。"""
    path = score_path(run_dir)
    scores = read_json(path)
    if len({s["case_id"] for s in scores}) != len(scores):
        raise ValueError("原评分存在重复题")
    frozen_path = run_dir / ("pre_scoring_prediction_freeze.json" if codex else "predictions_frozen.json")
    frozen = read_json(frozen_path)
    manifest_path = run_dir / "_private/manifest.json"
    manifest = read_json(manifest_path)
    cases = {c["case_id"]: c for c in manifest["cases"]}
    if codex:
        hashes = {Path(key.replace("\\", "/")).stem: value for key, value in frozen["sha256"].items()}
        details = {r["case_id"]: r for r in read_json(run_dir / "case_evaluation_details.json")}
        mode = "shared_codex_desktop"
    else:
        hashes = frozen["prediction_hashes"]
        details = {}
        mode = read_json(run_dir / "capture_config.json")["evaluation_mode"]
    selected = set(hashes) if case_ids is None else case_ids
    by_id = {s["case_id"]: s for s in scores}
    if not selected <= set(by_id) or not selected <= set(hashes):
        raise ValueError("选定题未在冻结预测和评分中齐备")
    rows = []
    for case_id in sorted(selected):
        prediction_path = run_dir / "predictions" / (case_id + ".json")
        if digest(prediction_path.read_bytes()) != hashes[case_id]:
            raise ValueError(f"{case_id}预测与原冻结哈希不符")
        record, score = read_json(prediction_path), by_id[case_id]
        if not codex and record["run_status"] != "success":
            raise ValueError("服务错误不可混入有效业务指标")
        if score["tool_calls"] != len(record["tool_calls"]) or score["elapsed_seconds"] != record["elapsed_seconds"]:
            raise ValueError("原评分的调用数或耗时与冻结预测不一致")
        row = {**score, "sample_id": cases[case_id]["sample_id"], "family_id": cases[case_id]["family_id"], "source_run": str(run_dir), "prompt_group": mode, "prediction_sha256": hashes[case_id], "score_source": str(path)}
        if codex:
            row.update(primary_success=bool(score["structure_complete"] and score["execution_ok"] and record["status"] == "completed" and score["protocol_compliant"]))
            row["strict_sql_success"] = row["primary_success"] and score["sql_structure_complete"]
            row.update({key: details[case_id][key] for key in ["retrieval_rounds_total", "first_complete_search_round", "first_complete_retrieval_call", "first_complete_mcp_call", "structure_read_calls"]})
            row.update(timing_source="desktop_host_Date.now_question_to_finish_record", observed_elapsed_seconds=None, capture_elapsed_seconds=None)
        else:
            terminal = read_json(run_dir / "traces" / case_id / "terminal.json")
            row.update(timing_source=record["timing_source"], observed_elapsed_seconds=record["observed_elapsed_seconds"], capture_elapsed_seconds=terminal.get("capture_elapsed_seconds"))
        rows.append(row)
    provenance = {
        "source_run": str(run_dir),
        "selected_case_ids": sorted(selected),
        "score_path": str(path),
        "score_sha256": digest(path.read_bytes()),
        "freeze_path": str(frozen_path),
        "freeze_sha256": digest(frozen_path.read_bytes()),
        "case_manifest_path": str(manifest_path),
        "case_manifest_sha256": digest(manifest_path.read_bytes()),
        "dataset_sha256": manifest["dataset_sha256"],
    }
    return rows, provenance


def deerflow_rows(run_dir: Path, *, final: bool) -> tuple[list[dict], list[dict]]:
    """组合154题有效旧结果与170题恢复结果；冻结前不读新评分。Args: run_dir/final。Returns: 指标和来源。"""
    selection = read_json(run_dir / "_private/retry_selection.json")
    source = Path(selection["source_run"])
    previous = read_json(source / "_private/retry_selection.json")
    original = Path(previous["source_run"])
    selected = {c["case_id"] for c in selection["selected_cases"]}
    old_ids = {c["case_id"] for c in selection["retained_cases"]}
    original_ids = {c["case_id"] for c in previous["retained_cases"]} - selected
    old_rows, old_origin = load_rows(source, old_ids)
    original_rows, original_origin = load_rows(original, original_ids)
    rows, origins = old_rows + original_rows, [old_origin, original_origin]
    if len(rows) != 154 or len(selected) != 170 or len({r["case_id"] for r in rows} | selected) != 324:
        raise ValueError("154题保留集合与170题恢复集合不满足324题分区")
    if final:
        if read_json(run_dir / "pipeline_status.json")["status"] != "complete":
            raise ValueError("恢复批次尚未完成评分")
        new_rows, new_origin = load_rows(run_dir, selected)
        rows.extend(new_rows)
        origins.append(new_origin)
    return rows, origins


def infrastructure_cost(run_dir: Path) -> dict:
    """另计无效基础设施尝试的耗时与调用，不计业务失败。Args: run_dir。Returns: 故障成本。"""
    selection = read_json(run_dir / "_private/retry_selection.json")
    source = Path(selection["source_run"])
    extra = {r["case_id"]: Path(r["source_run"]) for r in selection["extra_original_runtime_cases"]}
    old = []
    for case in selection["selected_cases"]:
        record = read_json(extra.get(case["case_id"], source) / "predictions" / (case["case_id"] + ".json"))
        if record["run_status"] == "success":
            raise ValueError("恢复集合包含正常业务结果")
        old.append(record)
    metadata = [read_json(p) for p in (run_dir / "infra_attempts").glob("*/*/metadata.json")]
    return {
        "historical_error_attempts": len(old),
        "historical_error_seconds": sum(r["elapsed_seconds"] for r in old),
        "historical_error_mcp_calls": sum(len(r["tool_calls"]) for r in old),
        "new_error_attempts": len(metadata),
        "new_error_seconds": sum(r["elapsed_seconds"] for r in metadata),
        "new_error_mcp_calls": sum(r["mcp_calls"] for r in metadata),
        "excluded_from_business_failure": True,
        "excluded_from_main_efficiency": True,
    }


def pct(value: float | None) -> str:
    """格式化比例。Args: value。Returns: 展示字符串。"""
    return f"{value:.2%}" if value is not None else "待恢复完成"


def num(value: float | None, digits: int = 3) -> str:
    """格式化计数或秒数。Args: value/digits。Returns: 展示字符串。"""
    return f"{value:.{digits}f}" if value is not None else "—（无适用样本）"


def render_report(result: dict) -> str:
    """生成四组清晰表头及检索轮数、方法说明。Args: result。Returns: Markdown。"""
    c, d = result["codex"], result["deerflow"]
    labels = [("Codex", "GPT-6.1-sol", "Medium", c), ("DeerFlow", "Qwen3.8-27B-FP8-Local-cloud", "Ultra / high", d)]
    lines = [
        "# VFSSQL MCP：Codex 与 DeerFlow 324题对照评估",
        "",
        f"生成时间：{result['generated_at_utc']}。状态：**{'全部有效预测已冻结并评分' if d else '模型服务异常恢复中，全量DeerFlow指标待完成'}**。",
        "",
        f"样本总数324；原有效并评分154题，新恢复有效预测{result['recovery_progress']['new_valid_predictions']}题，尚待恢复{result['recovery_progress']['pending_cases']}题。服务错误不计业务失败。",
        "",
        "## 1、首次候选发现",
        "",
        "| Harness | 模型 | 思考强度 | 表 R@1 | R@3 | R@5 | R@10 | 累计候选表 Recall | 累计候选表 Precision | 候选表 Macro-F1 | 候选完整率 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for harness, model, effort, metrics in labels:
        discovery = metrics["discovery"] if metrics else {}
        values = [discovery.get("first_recall_at_k", {}).get(str(k)) for k in (1, 3, 5, 10)] + [discovery.get(k) for k in ["candidate_table_recall", "candidate_table_precision", "candidate_table_macro_f1", "candidate_complete_rate"]]
        lines.append("| " + " | ".join([harness, model, effort] + [pct(v) for v in values]) + " |")
    lines += [
        "",
        "@k是首个成功且非空候选响应内、按返回顺序去重后的前k张表。逐题目标表召回后取均值，不是累计k次调用。R@5与R@10相同仅表示此批次没有增加目标命中，不能据此推断服务最大top_k。候选累计包含成功定位及结构读取实际暴露的表；目录初始化不计相关候选。",
        "",
        "## 2、表结构读取",
        "",
        "| Harness | 模型 | 思考强度 | 结构表 Recall | 结构表 Precision | 结构表 Macro-F1 | 目标字段 Recall | 目标表字段完整率 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for harness, model, effort, metrics in labels:
        structure = metrics["structure"] if metrics else {}
        values = [pct(structure.get(k)) for k in ["table_recall", "table_precision", "table_macro_f1", "column_recall", "complete_rate"]]
        if metrics:
            values[-1] += f"（{metrics['task']['counts']['structure_complete']}/{metrics['n']}）"
        lines.append("| " + " | ".join([harness, model, effort] + values) + " |")
    lines += [
        "",
        "字段Recall逐题计算已覆盖的目标(表,字段)对数/全部目标对数后平均。完整率逐题取0/1，要求所有目标表和字段齐备。两种表Macro-F1均先逐题计算2PR/(P+R)，再按题平均；不是对汇总P/R取调和均值，也不是family宏平均。Precision仅衡量相对既定标签的额外返回/读取量，不能证明额外表语义无关。",
        "",
        "## 3、SQL可执行性与任务成功",
        "",
        "| Harness | 模型 | 思考强度 | 首次 SQL 无错率 | 最终 SQL 无错率 | 用户任务成功率 | 显式 SQL 目标使用联合率 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for harness, model, effort, metrics in labels:
        values = []
        for metric, count in [("first_sql_error_free_rate", "first_execution_ok"), ("final_sql_error_free_rate", "execution_ok"), ("primary_success_rate", "primary_success"), ("strict_sql_joint_rate", "strict_sql_success")]:
            values.append(pct(metrics["task"][metric]) + f"（{metrics['task']['counts'][count]}/{metrics['n']}）" if metrics else "待恢复完成")
        lines.append("| " + " | ".join([harness, model, effort] + values) + " |")
    lines += [
        "",
        "首次/最终分别指第一条及最后一条真实sql_execute请求，不把从未执行SQL计作无错；分母均为324。用户任务成功要求真实检索完整覆盖目标表字段且最终SQL无错，DeerFlow还核验自然终态、最终答复、输入协议及证据重放。空结果允许成功，不比较Gold结果值。显式SQL联合率在主成功基础上，进一步要求最终实际SQL明确引用全部目标表字段；纯记忆完成且没有合格检索不计MCP主成功。",
        "",
        "## 4、效率和成本",
        "",
        "| Harness | 模型 | 思考强度 | 全样本平均保存耗时（秒/题） | P50（秒） | P95（秒） | 全样本平均 MCP（次/题） | 成功样本平均保存耗时（秒/题） | 成功样本平均 MCP（次/题） |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for harness, model, effort, metrics in labels:
        if metrics:
            e = metrics["efficiency"]
            values = [num(e["saved_elapsed"][k]) for k in ["mean_seconds", "p50_seconds", "p95_seconds"]] + [num(e["mean_mcp_calls"], 2), num(e["successful_saved_elapsed"]["mean_seconds"]), num(e["successful_mean_mcp_calls"], 2)]
        else:
            values = ["待恢复完成"] * 6
        lines.append("| " + " | ".join([harness, model, effort] + values) + " |")
    lines += [
        "",
        "这里的“保存耗时”是每题已保存的elapsed_seconds，非写盘耗时。Codex原记录采用Date.now，从宿主获取本题后到保存预测前，处于共享会话且部分变体复用计划；"
        "DeerFlow采用Gateway run.created_at→updated_at，覆盖该独立对话的运行，缺失时回退宿主观察时间。两者计时范围不同，不能仅凭此表推断模型推理速度。"
        "DeerFlow另外保存宿主观察时长和完整采集时长，详见metrics.json/逐题文件。P50采用中位数，P95采用最近秩ceil(0.95*n)。成功样本指本报告的主任务成功。",
        "",
        "MCP次数包括主/子代理实际VFSSQL调用、失败、重试、枚举及SQL，不因调用失败而扣除；本地CLI/记忆读取等非VFSSQL调用不计MCP。基础设施无效尝试单独列成本，不混入主业务效率分母。没有token账单与统一定价数据，不估算金额。",
        "",
        "## 5、多少轮检索完成",
        "",
        "| Harness | 累计定位轮数 | 平均轮数/题 | 成功题平均轮数 | 结构完整题数 | 首次完整时定位轮数分布 | 最终仍未完整 |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for harness, _, _, metrics in labels:
        if metrics:
            r = metrics["rounds"]
            dist = "；".join(f"{key}轮：{value}题" for key, value in r["first_complete_search_round_distribution"].items()) or "无"
            values = [str(r["total_search_rounds"]), num(r["mean_search_rounds"]), num(r["successful_mean_search_rounds"]), str(r["structure_complete_cases"]), dist, str(r["never_complete"])]
        else:
            values = ["待恢复完成"] * 6
        lines.append("| " + " | ".join([harness] + values) + " |")
    lines += [
        "",
        "每个find/grep/search请求及返回记一轮定位，失败与重试也计数；结构读取次数另存。逐题记录首次完整时的定位轮数、检索调用序号和MCP序号。0轮可能代表直接读取结构；没有完整结构保留null，不能当作0轮成功。",
        "",
        "## 6、恢复成本与实验条件",
        "",
    ]
    infra = result["infrastructure"]
    lines += [
        f"- 历史待恢复：{infra['historical_error_attempts']}次运行异常，保存耗时合计{infra['historical_error_seconds']:.3f}秒、{infra['historical_error_mcp_calls']}次MCP；其中168题模型HTTP502发生于MCP调用前，另2题为原保留组运行异常。",
        f"- 新批次已归档基础设施异常：{infra['new_error_attempts']}次，{infra['new_error_seconds']:.3f}秒、{infra['new_error_mcp_calls']}次MCP。这些尝试保留为故障成本；正常完成的业务失败不重做择优。",
        "- DeerFlow：固定并发1，Ultra、reasoning_effort=high，thinking/is_plan_mode/subagent_enabled=true；等待模型自然终态，耗时和调用数仅记录。实际配置不是xhigh。",
        "- 最终324题由原79题自主发现Skill、75题已正常完成的显式Skill复测、170题显式Skill恢复组合。即79题仅原问题，245题带原生/vfssql-retrieval及固定必须使用提示；这不是统一提示下的一次全新324题实验。",
        "- Codex为共享会话基线，DeerFlow逐题独立对话；模型、宿主、上下文、Skill提示及计时范围均有差异，结果衡量这些条件的组合。",
        "- Codex数据已核对原冻结预测及评分；其旧记录不含DeerFlow式原生SSE重放。DeerFlow保留原始SSE、分页API回执和哈希审计。",
        "- 本报告只读取冻结评分及证据，不把评估标签、Gold SQL或逐题分数反馈给正在运行的DeerFlow。完整来源与SHA-256见input_manifest.json，逐题指标见case_metrics.json/csv。",
        "",
    ]
    if not d:
        s = result["retained_deerflow"]
        lines += [
            "## 当前有效旧记录（非最终324题指标）",
            "",
            f"仅已有154题已评分结果：主成功{s['task']['counts']['primary_success']}/154，其余{s['n'] - s['task']['counts']['primary_success']}题为正常完成的业务失败。"
            "新恢复预测在170题全部完成、统一冻结后评分；本页DeerFlow全量指标保持待完成，不以服务错误补0。",
            "",
        ]
    return "\n".join(lines)


def write_comparison(run_dir: Path, *, final: bool = False, codex_run: Path = CODEX_RUN) -> dict:
    """生成独立对照产物，保留既有预测与评分。Args: run_dir/final/codex_run。Returns: 汇总。"""
    codex_rows, codex_origin = load_rows(codex_run, codex=True)
    rows, origins = deerflow_rows(run_dir, final=final)
    if len(codex_rows) != 324 or final and {r["case_id"] for r in rows} != {r["case_id"] for r in codex_rows}:
        raise ValueError("对照题数或题号不一致")
    codex_samples = {r["case_id"]: r["sample_id"] for r in codex_rows}
    if any(r["sample_id"] != codex_samples[r["case_id"]] for r in rows) or any(p["dataset_sha256"] != codex_origin["dataset_sha256"] for p in origins):
        raise ValueError("对照样本身份或数据集版本不一致")
    selected = {c["case_id"] for c in read_json(run_dir / "_private/retry_selection.json")["selected_cases"]}
    valid = [read_json(p) for p in (run_dir / "predictions").glob("case-*.json")]
    new_valid = sum(r["case_id"] in selected and r["run_status"] == "success" for r in valid)
    code_paths = [Path(__file__)]
    input_manifest = {"generated_at_utc": utc_now(), "codex": codex_origin, "deerflow": origins, "report_code_hashes": {p.name: digest(p.read_bytes()) for p in code_paths}, "new_predictions_scored_only_after_full_freeze": True}
    result = {
        "generated_at_utc": utc_now(),
        "status": "complete" if final else "recovery_in_progress",
        "dataset_n": 324,
        "codex": summarize_rows(codex_rows),
        "deerflow": summarize_rows(rows) if final else None,
        "retained_deerflow": None if final else summarize_rows(rows),
        "recovery_progress": {"new_valid_predictions": new_valid, "planned_recovery_cases": len(selected), "pending_cases": len(selected) - new_valid},
        "infrastructure": infrastructure_cost(run_dir),
        "definitions_version": "vfssql-comparison-1.0",
    }
    output = run_dir / "comparison_metrics"
    output.mkdir(exist_ok=True)
    text = render_report(result)
    case_rows = [{"harness": "Codex", **r} for r in codex_rows] + [{"harness": "DeerFlow", **r} for r in rows]
    columns = [
        "harness",
        "case_id",
        "sample_id",
        "family_id",
        "source_run",
        "prompt_group",
        "candidate_table_recall",
        "candidate_table_precision",
        "candidate_complete",
        "table_recall",
        "retrieved_table_precision",
        "column_coverage",
        "structure_complete",
        "first_execution_ok",
        "execution_ok",
        "primary_success",
        "strict_sql_success",
        "elapsed_seconds",
        "observed_elapsed_seconds",
        "capture_elapsed_seconds",
        "timing_source",
        "tool_calls",
        "sql_executions",
        "retrieval_rounds_total",
        "structure_read_calls",
        "first_complete_search_round",
        "first_complete_retrieval_call",
        "first_complete_mcp_call",
        "prediction_sha256",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(case_rows)
    for folder in [output] + ([output / (utc_now().replace(":", "").replace("-", "").split(".")[0] + "-" + uuid4().hex[:8])] if final else []):
        folder.mkdir(exist_ok=True)
        write_json(folder / "metrics.json", result)
        write_json(folder / "input_manifest.json", input_manifest)
        write_json(folder / "case_metrics.json", case_rows)
        (folder / "case_metrics.csv").write_text(buffer.getvalue(), encoding="utf-8-sig", newline="")
        (folder / "report.md").write_text(text, encoding="utf-8", newline="\n")
    return {"status": result["status"], "report": str(output / "report.md"), "codex_n": 324, "deerflow_scored_n": len(rows), "new_valid_predictions": new_valid}


def main() -> None:
    """对照报告命令入口。Args: 命令行参数。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--codex-run", type=Path, default=CODEX_RUN)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    print(json.dumps(write_comparison(args.run_dir.resolve(), final=args.final, codex_run=args.codex_run.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
