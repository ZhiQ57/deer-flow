"""迁移样本，冻结原生 Skill 审计快照；答题消息仅为问题原文。"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

from eval_common import CONTEXT, DATASET_SHA256, EXPLICIT_MODE, EXPLICIT_SKILL_PREFIX, NATIVE_BUDGETS, NATIVE_MODE, RETRIEVAL_TOOLS, ROOT, code_hashes, digest, git_revision, read_json, skill_hashes, utc_now, write_json

PUBLIC_SKILL = ROOT.parents[1] / "skills/public/z-vfssql-retrieval"


def migrate(dataset: Path) -> None:
    """核对并复制既定题库。Args: dataset 源文件。Returns: 无。"""
    if digest(dataset.read_bytes()) != DATASET_SHA256:
        raise ValueError("样本与既定冻结版本不符")
    destinations = [(dataset, ROOT / "datasets/vfssql-evaluation-dataset.v1.json")]
    for source, target in destinations:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if digest(target.read_bytes()) != digest(source.read_bytes()):
                raise ValueError(f"已有快照内容不同：{target.name}")
        else:
            shutil.copyfile(source, target)


def prepare(run_dir: Path, *, pilot: bool = False, base_url: str = "http://127.0.0.1:8001", native_skill: Path = PUBLIC_SKILL, retry_source: Path | None = None, retry_kind: str = "no_retrieval") -> dict:
    """准备不可覆盖的一轮输入。Args: run_dir/pilot/base_url/native_skill。Returns: 无标签摘要。"""
    dataset = ROOT / "datasets/vfssql-evaluation-dataset.v1.json"
    samples = read_json(dataset)
    if digest(dataset.read_bytes()) != DATASET_SHA256 or len(samples) != 324 or len({s["id"] for s in samples}) != 324:
        raise ValueError("样本版本或唯一性不符")
    native_skill = native_skill.resolve()
    hashes = skill_hashes(native_skill)
    bundle_sha = digest(json.dumps(hashes, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    snapshot_root = ROOT / "skills" / ("native-" + bundle_sha)
    for relative, expected in hashes.items():
        target = snapshot_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and digest(target.read_bytes()) != expected:
            raise ValueError("原生 Skill 审计快照内容变化")
        if not target.exists():
            shutil.copyfile(native_skill / relative, target)
    indices = list(range(len(samples)))
    random.Random(20261015).shuffle(indices)
    source_cases, selected_cases, retained_cases = None, None, None
    extra_runtime_cases = []
    if retry_source is not None:
        retry_source = retry_source.resolve()
        old_config = read_json(retry_source / "capture_config.json")
        source_cases = read_json(retry_source / "_private/manifest.json")["cases"]
        frozen = read_json(retry_source / "predictions_frozen.json")["prediction_hashes"]
        if old_config["dataset_sha256"] != DATASET_SHA256 or old_config["context"] != CONTEXT or old_config["native_skill"]["files"] != hashes:
            raise ValueError("复测源的数据集、模型参数或 Skill 与当前配置不同")
        selected_cases, retained_cases = [], []
        for case in source_cases:
            path = retry_source / "predictions" / f"{case['case_id']}.json"
            if digest(path.read_bytes()) != frozen[case["case_id"]]:
                raise ValueError("复测源预测与冻结哈希不一致")
            record = read_json(path)
            if retry_kind not in {"no_retrieval", "runtime_error"}:
                raise ValueError("未知复测筛选条件")
            selected = record["run_status"] == "error" if retry_kind == "runtime_error" else not any(call["tool"] in RETRIEVAL_TOOLS for call in record["tool_calls"])
            (selected_cases if selected else retained_cases).append(case)
        if retry_kind == "runtime_error":
            # 原81题保留组的未完成运行也不能当作业务失败，按同一恢复原则补入。
            prior_selection = read_json(retry_source / "_private/retry_selection.json")
            prior_run = Path(prior_selection["source_run"])
            prior_frozen = read_json(prior_run / "predictions_frozen.json")["prediction_hashes"]
            for case in prior_selection["retained_cases"]:
                path = prior_run / "predictions" / f"{case['case_id']}.json"
                if digest(path.read_bytes()) != prior_frozen[case["case_id"]]:
                    raise ValueError("原保留组冻结预测哈希不一致")
                if read_json(path)["run_status"] == "error":
                    selected_cases.append(case)
                    source_cases.append(case)
                    extra_runtime_cases.append({**case, "source_run": str(prior_run)})
        if not selected_cases:
            raise ValueError("复测源没有缺检索样本")
        indices = [case["dataset_index"] for case in selected_cases]
    if pilot:
        # 标签仅用于宿主选择不同复杂度的联调题，不进入答题请求。
        single = next(i for i in indices if len(samples[i]["target_tables"]) == 1)
        multi = next(i for i in indices if len(samples[i]["target_tables"]) > 1)
        long = max((i for i in indices if i not in {single, multi}), key=lambda i: len(json.dumps(samples[i]["related_ddl"], ensure_ascii=False)))
        indices = [single, multi, long]
    questions, cases = [], []
    preview_families = {s["family_id"] for s in samples if s["id"] == "vfssql-s0059-01"}
    for order, index in enumerate(indices, start=1):
        sample = samples[index]
        case_id = next(case["case_id"] for case in source_cases if case["dataset_index"] == index) if source_cases else f"case-{order:04d}"
        questions.append({"case_id": case_id, "question": sample["question"]})
        cases.append({"case_id": case_id, "dataset_index": index, "sample_id": sample["id"], "family_id": sample["family_id"], "preview_family": sample["family_id"] in preview_families})
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in ["traces", "predictions", "scoring", "_private"]:
        (run_dir / name).mkdir()
    raw = "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions).encode("utf-8")
    (run_dir / "questions.jsonl").write_bytes(raw)
    write_json(run_dir / "_private/manifest.json", {"version": 3, "seed": 20261015, "dataset_sha256": DATASET_SHA256, "cases": cases}, exclusive=True)
    inputs = [dataset, *[snapshot_root / relative for relative in hashes]]
    config = {
        "version": 3,
        "evaluation_mode": EXPLICIT_MODE if retry_source else NATIVE_MODE,
        "capture_source": "deerflow_gateway",
        "created_at_utc": utc_now(),
        "purpose": "pilot" if pilot else "formal",
        "base_url": base_url.rstrip("/"),
        "context": CONTEXT,
        "budgets": NATIVE_BUDGETS,
        "concurrency": 1,
        "completion_policy": "wait_for_natural_terminal",
        "poll_interval_seconds": 5,
        "transport_retry_attempts": 4,
        "recursion_limit": 1000,
        "stream_mode": ["values", "messages-tuple", "custom", "updates"],
        "stream_subgraphs": True,
        "dataset_sha256": DATASET_SHA256,
        "skill_sha256": hashes["SKILL.md"],
        "skill_text_sha256": digest((native_skill / "SKILL.md").read_text(encoding="utf-8").encode("utf-8")),
        "skill_loading": "deerflow_slash_activation" if retry_source else "deerflow_autodiscovery",
        "native_skill": {"name": "vfssql-retrieval", "source_directory": str(native_skill), "files": hashes, "bundle_sha256": bundle_sha},
        "questions_sha256": digest(raw),
        "manifest_sha256": digest((run_dir / "_private/manifest.json").read_bytes()),
        "input_hashes": {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in inputs},
        "code_hashes": code_hashes(),
        "repository_revision": git_revision(),
        "planned_cases": len(cases),
        "server_revision": None,
        "index_snapshot": None,
    }
    if retry_source:
        config.update(prompt_prefix=EXPLICIT_SKILL_PREFIX, retry_source=str(retry_source), selected_cases=len(selected_cases), retained_cases=len(retained_cases), retry_selection="runtime_error_pending_recovery" if retry_kind == "runtime_error" else "no_find_grep_or_content_read; tree_folder_is_initialization")
        if retry_kind == "runtime_error":
            config.update(provider_recovery=True, defer_prediction_freeze=True, provider_backoff_seconds=[60, 120, 300, 600], recovery_authorized_at="2026-10-26", retry_valid_business_failures=False)
        write_json(run_dir / "_private/retry_selection.json", {"source_run": str(retry_source), "source_predictions_frozen_sha256": digest((retry_source / "predictions_frozen.json").read_bytes()), "selected_cases": selected_cases, "retained_cases": retained_cases, "extra_original_runtime_cases": extra_runtime_cases, "actual_run_cases": [case["case_id"] for case in cases]}, exclusive=True)
    write_json(run_dir / "capture_config.json", config, exclusive=True)
    write_json(
        run_dir / "run_metadata.json",
        {
            k: config[k]
            for k in ["created_at_utc", "purpose", "evaluation_mode", "completion_policy", "concurrency", "context", "budgets", "repository_revision", "planned_cases", "server_revision", "index_snapshot", "skill_loading", "native_skill"]
        },
        exclusive=True,
    )
    # 同时保存可独立运行的代码和输入，后续修订不覆盖既有运行协议。
    frozen_root = run_dir / "_private/frozen_evaluator"
    files = sorted(set(config["code_hashes"]) | set(config["input_hashes"]) | {"metrics-provenance.json"})
    for relative in files:
        target = frozen_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    write_json(run_dir / "_private/evaluator_snapshot.json", {"files": {r: digest((frozen_root / r).read_bytes()) for r in files}}, exclusive=True)
    return {"run_dir": str(run_dir), "cases": len(cases), "families": len({s["family_id"] for s in samples}), "dataset_sha256": DATASET_SHA256}


def build_prompt(question: dict, config: dict) -> str:
    """仅返回原问题；题号及配置不进入消息。Args: question/config。Returns: 未改写题面。"""
    if set(question) != {"case_id", "question"} or not isinstance(question["question"], str):
        raise ValueError("题面投影越界")
    if config.get("evaluation_mode") == EXPLICIT_MODE:
        if config.get("prompt_prefix") != EXPLICIT_SKILL_PREFIX:
            raise ValueError("显式 Skill 提示与冻结协议不同")
        return EXPLICIT_SKILL_PREFIX + question["question"]
    if config.get("evaluation_mode") != NATIVE_MODE:
        raise ValueError("旧协议只能使用该轮归档采集器恢复")
    return question["question"]


def main() -> None:
    """准备命令入口。Args: 命令行参数。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--native-skill", type=Path, default=PUBLIC_SKILL)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    migrate(args.dataset)
    print(json.dumps(prepare(args.run_dir.resolve(), pilot=args.pilot, base_url=args.base_url, native_skill=args.native_skill), ensure_ascii=False))


if __name__ == "__main__":
    main()
