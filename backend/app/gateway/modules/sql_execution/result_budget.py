"""Gateway SQL Execution 结果序列化与预算控制。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal
from typing import Any

from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig


def _json_value(value: Any, *, max_chars: int) -> Any:
    """把数据库单元格转换成受预算保护的 JSON 值。

    Args:
        value: 数据库驱动返回的原始值。
        max_chars: 字符串单元格最大长度。

    Returns:
        JSON 可序列化的标量值。
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def bound_result(
    raw_rows: list[Any],
    columns: list[str],
    *,
    config: DataQueryServiceAbilityConfig,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    """统一处理数据库行并施加结果预算。

    Args:
        raw_rows: 数据库驱动返回的原始行。
        columns: 查询结果列名。
        config: 当前 DataAgent 查询能力配置。
        validation: 当前 SQL 校验结果。

    Returns:
        JSON 安全且受预算限制的查询结果。
    """
    capped_rows = raw_rows[: config.sql_execution.max_rows]
    values: list[dict[str, Any]] = []
    for raw_row in capped_rows:
        if isinstance(raw_row, Mapping):
            row = {
                str(key): _json_value(
                    value,
                    max_chars=config.sql_execution.max_cell_chars,
                )
                for key, value in raw_row.items()
            }
        else:
            row = {
                columns[index] if index < len(columns) else f"column_{index + 1}": (
                    _json_value(
                        value,
                        max_chars=config.sql_execution.max_cell_chars,
                    )
                )
                for index, value in enumerate(raw_row)
            }
        values.append(row)

    bounded_values: list[dict[str, Any]] = []
    used_chars = 2
    for row in values:
        row_chars = len(json.dumps(row, ensure_ascii=False, default=str)) + 1
        if used_chars + row_chars > config.sql_execution.max_result_chars:
            break
        bounded_values.append(row)
        used_chars += row_chars
    truncated = len(raw_rows) > config.sql_execution.max_rows or len(bounded_values) < len(values) or (validation.get("row_limit_applied") is True and len(values) >= config.sql_execution.max_rows)
    return {
        "row_count": len(values),
        "returned_row_count": len(bounded_values),
        "columns": columns,
        "rows": bounded_values,
        "truncated": truncated,
        "empty": not values,
    }
