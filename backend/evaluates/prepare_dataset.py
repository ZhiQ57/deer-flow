"""迁移样本和 Skill，准备独立对话的冻结运行输入。"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

from eval_common import BUDGETS, CONTEXT, DATASET_SHA256, ROOT, SKILL_SHA256, code_hashes, digest, git_revision, read_json, utc_now, write_json


def migrate(dataset: Path, skill: Path) -> None:
    """核对并复制上轮输入。Args: dataset/skill 源文件。Returns: 无。"""
    if digest(dataset.read_bytes()) != DATASET_SHA256 or digest(skill.read_bytes()) != SKILL_SHA256:
        raise ValueError("样本或 Skill 与上轮冻结版本不符")
    destinations = [(dataset, ROOT / "datasets/vfssql-evaluation-dataset.v1.json")]
    destinations += [(p, ROOT / "skills/vfssql-retrieval" / p.relative_to(skill.parent)) for p in [skill, *sorted((skill.parent / "references").glob("*.md"))]]
    for source, target in destinations:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if digest(target.read_bytes()) != digest(source.read_bytes()):
                raise ValueError(f"已有快照内容不同：{target.name}")
        else:
            shutil.copyfile(source, target)


def prepare(run_dir: Path, *, pilot: bool = False, base_url: str = "http://127.0.0.1:8001") -> dict:
    """准备不可覆盖的一轮输入。Args: run_dir/pilot/base_url。Returns: 无标签摘要。"""
    dataset = ROOT / "datasets/vfssql-evaluation-dataset.v1.json"
    samples = read_json(dataset)
    if digest((ROOT / "skills/vfssql-retrieval/SKILL.md").read_bytes()) != SKILL_SHA256:
        raise ValueError("Skill 快照与上轮冻结版本不符")
    if digest(dataset.read_bytes()) != DATASET_SHA256 or len(samples) != 324 or len({s["id"] for s in samples}) != 324:
        raise ValueError("样本版本或唯一性不符")
    indices = list(range(len(samples)))
    random.Random(20261015).shuffle(indices)
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
        case_id = f"case-{order:04d}"
        questions.append({"case_id": case_id, "question": sample["question"]})
        cases.append({"case_id": case_id, "dataset_index": index, "sample_id": sample["id"], "family_id": sample["family_id"], "preview_family": sample["family_id"] in preview_families})
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in ["traces", "predictions", "scoring", "_private"]:
        (run_dir / name).mkdir()
    raw = "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions).encode("utf-8")
    (run_dir / "questions.jsonl").write_bytes(raw)
    write_json(run_dir / "_private/manifest.json", {"version": 3, "seed": 20261015, "dataset_sha256": DATASET_SHA256, "cases": cases}, exclusive=True)
    inputs = [dataset, ROOT / "evaluation-prompt.md", *sorted((ROOT / "skills/vfssql-retrieval").rglob("*.md"))]
    config = {
        "version": 1,
        "capture_source": "deerflow_gateway",
        "created_at_utc": utc_now(),
        "purpose": "pilot" if pilot else "formal",
        "base_url": base_url.rstrip("/"),
        "context": CONTEXT,
        "budgets": BUDGETS,
        "recursion_limit": 1000,
        "stream_mode": ["values", "messages-tuple", "custom", "updates"],
        "stream_subgraphs": True,
        "dataset_sha256": DATASET_SHA256,
        "skill_sha256": SKILL_SHA256,
        "skill_loading": "explicit_text_injection",
        "questions_sha256": digest(raw),
        "manifest_sha256": digest((run_dir / "_private/manifest.json").read_bytes()),
        "input_hashes": {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in inputs},
        "code_hashes": code_hashes(),
        "repository_revision": git_revision(),
        "planned_cases": len(cases),
        "server_revision": None,
        "index_snapshot": None,
    }
    write_json(run_dir / "capture_config.json", config, exclusive=True)
    write_json(run_dir / "run_metadata.json", {k: config[k] for k in ["created_at_utc", "purpose", "context", "budgets", "repository_revision", "planned_cases", "server_revision", "index_snapshot", "skill_loading"]}, exclusive=True)
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
    """仅将当前题面和固定 Skill 注入。Args: question/config。Returns: 完整提示词。"""
    if set(question) != {"case_id", "question"}:
        raise ValueError("题面投影越界")
    skill_root = ROOT / "skills/vfssql-retrieval"
    files = [skill_root / "SKILL.md", *sorted((skill_root / "references").glob("*.md"))]
    sections = [f"\n## Skill 文件：{p.relative_to(skill_root).as_posix()}\n{p.read_text(encoding='utf-8-sig')}" for p in files]
    return (
        (ROOT / "evaluation-prompt.md").read_text(encoding="utf-8")
        + "\n\n## 本题预算\n"
        + json.dumps(config["budgets"], ensure_ascii=False)
        + "\n\n## 当前题\n"
        + json.dumps(question, ensure_ascii=False)
        + "\n\n## 本轮唯一指定 Skill（含引用原文）\n"
        + "\n".join(sections)
    )


def main() -> None:
    """准备命令入口。Args: 命令行参数。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--skill", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    migrate(args.dataset, args.skill)
    print(json.dumps(prepare(args.run_dir.resolve(), pilot=args.pilot, base_url=args.base_url), ensure_ascii=False))


if __name__ == "__main__":
    main()
