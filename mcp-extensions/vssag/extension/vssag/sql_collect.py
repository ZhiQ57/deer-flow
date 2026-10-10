"""VSSAG SQL Schema 独立一次性采集脚本。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .client import VSSAGClient


def _load_overrides(
    overrides_json: str | None,
    overrides_file: str | None,
) -> dict[str, Any] | None:
    """读取一次性任务的 Setting 覆盖值。"""

    if overrides_json and overrides_file:
        raise ValueError("--overrides-json 与 --overrides-file 不能同时使用")
    if overrides_file:
        with Path(overrides_file).open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    elif overrides_json:
        value = json.loads(overrides_json)
    else:
        return None
    if not isinstance(value, dict):
        raise ValueError("Setting 覆盖值必须是 JSON 对象")
    return value


async def collect_sql_schema(
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """使用当前 YAML 默认 Setting 执行一次 SQL Schema 采集。"""

    async with VSSAGClient.from_env(setting_overrides=overrides) as client:
        collection = await client.collect_sql_schema()
        return {
            "success": True,
            "backend": "vssag",
            "db_type": collection.db_type,
            "table_count": len(collection.tables),
            "field_value_count": len(collection.collected_field_values),
            "graph_edge_count": len(collection.graph_edges),
            "evidence_count": len(collection.evidence),
        }


def main() -> int:
    """解析命令行参数并输出脱敏采集摘要。"""

    parser = argparse.ArgumentParser(
        description="使用 VSSAG 默认配置执行一次 SQL Schema 采集"
    )
    parser.add_argument(
        "--overrides-json",
        help="本次任务的 Setting 覆盖 JSON，不包含 API Key 等敏感值",
    )
    parser.add_argument(
        "--overrides-file",
        help="本次任务的 Setting 覆盖 JSON 文件",
    )
    args = parser.parse_args()
    try:
        overrides = _load_overrides(args.overrides_json, args.overrides_file)
        result = asyncio.run(collect_sql_schema(overrides))
    except Exception as exc:
        print(
            json.dumps(
                {"success": False, "backend": "vssag", "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
