"""LLM JSON 输出解析适配。"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

import json_repair


class LLMJSONParseError(ValueError):
    """LLM 输出无法恢复为符合基本结构要求的 JSON。"""


def parse_llm_json(
    text: str,
    *,
    required_keys: Collection[str] = (),
) -> Any:
    """使用成熟的 ``json-repair`` 解析 LLM 返回的 JSON。

    Args:
        text: LLM 返回的原始文本。
        required_keys: 解析结果必须包含的顶层键。

    Returns:
        ``json-repair`` 恢复后的 Python 对象。

    Raises:
        LLMJSONParseError: 返回为空、无法解析或不满足顶层结构要求。
    """
    if not isinstance(text, str) or not text.strip():
        raise LLMJSONParseError("LLM 响应为空")
    try:
        payload = json_repair.loads(text)
    except Exception as exc:
        raise LLMJSONParseError("LLM 响应无法解析为 JSON") from exc
    if payload is None or (
        isinstance(payload, str) and not payload.strip()
    ):
        raise LLMJSONParseError("LLM 响应未返回 JSON 内容")
    missing_keys = set(required_keys) - (
        set(payload) if isinstance(payload, Mapping) else set()
    )
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        raise LLMJSONParseError(f"LLM JSON 缺少必需字段: {missing}")
    return payload
