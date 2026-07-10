"""DataAgent 图表规格构造。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:[T\s].*)?$")
_SUPPORTED_CHART_TYPES = frozenset({"auto", "bar", "line", "pie", "scatter", "table", "kpi"})


def _is_numeric(value: Any) -> bool:
    """判断值是否为可绘图数值。

    Args:
        value: 单元格值。

    Return:
        可解释为数值时返回 True。
    """
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return False
    return parsed.is_finite()


def _is_date_like(value: Any) -> bool:
    """判断值是否为 ISO 日期/时间文本。

    Args:
        value: 单元格值。

    Return:
        日期/时间文本返回 True。
    """
    return isinstance(value, str) and bool(_DATE_PATTERN.match(value.strip()))


def _column_matches(rows: list[Mapping[str, Any]], column: str, predicate) -> bool:
    """判断列中的非空值是否都满足谓词。

    Args:
        rows: 结果行。
        column: 列名。
        predicate: 值判断函数。

    Return:
        至少存在一个非空值且全部满足时返回 True。
    """
    values = [row.get(column) for row in rows if row.get(column) is not None]
    return bool(values) and all(predicate(value) for value in values)


def build_chart_spec(
    execution: Mapping[str, Any],
    *,
    chart_type: str = "auto",
    x: str | None = None,
    y: list[str] | None = None,
    series: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """根据 SQL 结果构造稳定图表规格。

    Args:
        execution: `data_execute_sql` 的执行结果。
        chart_type: 图表类型，支持 auto/bar/line/pie/scatter/table/kpi。
        x: X 轴字段。
        y: Y 轴字段列表。
        series: 可选系列字段。
        title: 可选标题。

    Return:
        DataAgent 图表规格。
    """
    normalized_type = chart_type.strip().lower()
    if normalized_type not in _SUPPORTED_CHART_TYPES:
        raise ValueError(f"不支持的图表类型：{chart_type}。")
    if not execution.get("ok"):
        raise ValueError("只有成功的 SQL 执行结果才能生成 ChartSpec。")

    columns = [str(column) for column in execution.get("columns") or []]
    rows = [row for row in execution.get("rows") or [] if isinstance(row, Mapping)]
    if not columns:
        raise ValueError("SQL 结果没有可用列。")

    numeric_columns = [column for column in columns if _column_matches(rows, column, _is_numeric)]
    date_columns = [column for column in columns if _column_matches(rows, column, _is_date_like)]

    resolved_x = x
    resolved_y = list(y or [])
    if resolved_x is not None and resolved_x not in columns:
        raise ValueError(f"X 轴字段不存在：{resolved_x}。")
    if series is not None and series not in columns:
        raise ValueError(f"系列字段不存在：{series}。")
    if not resolved_y:
        resolved_y = numeric_columns[:2]
    missing_y = [column for column in resolved_y if column not in columns]
    if missing_y:
        raise ValueError(f"Y 轴字段不存在：{', '.join(missing_y)}。")
    non_numeric_y = [column for column in resolved_y if column not in numeric_columns]
    if normalized_type != "table" and non_numeric_y:
        raise ValueError(f"非表格图表的 Y 轴必须是数值字段：{', '.join(non_numeric_y)}。")

    if resolved_x is None:
        candidates = [column for column in columns if column not in resolved_y]
        resolved_x = (date_columns or candidates or [None])[0]

    if normalized_type == "auto":
        if len(rows) <= 1 and resolved_y:
            normalized_type = "kpi"
            resolved_x = None
        elif resolved_x in date_columns and resolved_y:
            normalized_type = "line"
        elif resolved_x and resolved_y:
            normalized_type = "bar"
        else:
            normalized_type = "table"

    if normalized_type in {"bar", "line", "pie"} and (not resolved_x or not resolved_y):
        raise ValueError(f"{normalized_type} 图需要 X 轴和至少一个数值 Y 轴。")
    if normalized_type == "scatter":
        if not resolved_x or resolved_x not in numeric_columns or not resolved_y:
            raise ValueError("scatter 图需要数值 X 轴和至少一个数值 Y 轴。")
    if normalized_type == "kpi" and not resolved_y:
        raise ValueError("kpi 图需要至少一个数值字段。")

    encoding: dict[str, Any] = {}
    if resolved_x:
        encoding["x"] = {"field": resolved_x, "type": "temporal" if resolved_x in date_columns else "nominal"}
    if resolved_y:
        encoding["y"] = [{"field": column, "type": "quantitative"} for column in resolved_y]
    if series:
        encoding["series"] = {"field": series, "type": "nominal"}

    return {
        "type": normalized_type,
        "title": title or "DataAgent 查询结果",
        "x": resolved_x,
        "y": resolved_y,
        "series": series,
        "encoding": encoding,
        "data": [dict(row) for row in rows[:50]],
        "row_count": int(execution.get("row_count") or len(rows)),
        "truncated": bool(execution.get("truncated")),
    }
