"""执行完整评估流水线：逐题采集、冻结全部预测、离线评分和报告。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from eval_common import check_frozen, utc_now, write_json
from run_deerflow import run
from score_results import score


def pipeline(run_dir: Path) -> None:
    """运行全部步骤并持久化进度。Args: run_dir 冻结运行目录。Returns: 无。"""
    config = check_frozen(run_dir)
    state = {"pid": os.getpid(), "started_at_utc": utc_now(), "planned_cases": config["planned_cases"], "purpose": config["purpose"], "status": "running"}
    write_json(run_dir / "pipeline_status.json", state)
    try:
        if config.get("provider_recovery"):
            from provider_recovery import recover

            asyncio.run(recover(run_dir))
        else:
            asyncio.run(run(run_dir, None))
        state.update(status="scoring", updated_at_utc=utc_now())
        write_json(run_dir / "pipeline_status.json", state)
        summary = score(run_dir)
        state.update(status="complete", finished_at_utc=utc_now(), summary=summary, report="report.md")
        write_json(run_dir / "pipeline_status.json", state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
    except BaseException as error:
        state.update(status="needs_attention", updated_at_utc=utc_now(), error_type=type(error).__name__, error=str(error))
        write_json(run_dir / "pipeline_status.json", state)
        raise


def main() -> None:
    """完整流水线入口。Args: 命令行参数。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    pipeline(args.run_dir.resolve())


if __name__ == "__main__":
    main()
