"""MCP 工具返回值序列化辅助。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any


def to_jsonable(value: Any) -> Any:
    """把 SDK 返回对象转换为 MCP 可序列化结构。

    Args:
        value: 任意 SDK 返回对象。

    Returns:
        JSON 友好的基础类型、列表或字典。
    """
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
