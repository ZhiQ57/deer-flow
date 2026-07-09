"""JSON / dict 序列化辅助工具。"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    """把 dataclass 对象转换为字典。

    Args:
        value: dataclass 实例。

    Returns:
        dataclass 字段字典。
    """
    if not is_dataclass(value):
        raise TypeError("value must be a dataclass instance")
    result = asdict(value)
    if not isinstance(result, dict):
        raise TypeError("dataclass instance must serialize to a mapping")
    return result


def json_dumps(value: Any) -> str:
    """使用 TableRAG 默认参数序列化 JSON。

    Args:
        value: 待序列化对象。

    Returns:
        UTF-8 友好的 JSON 字符串。
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)