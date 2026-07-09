"""通用名称、字符串和参数校验工具。"""

from __future__ import annotations

import re

_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def validate_safe_identifier(value: str, label: str = "identifier") -> None:
    """校验通用数据库标识符是否只包含安全字符。

    Args:
        value: 待校验标识符。
        label: 错误信息中的字段名。

    Returns:
        None。
    """
    if not isinstance(value, str) or not _SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label} must be a safe identifier, got: {value!r}")


def require_non_empty_string(value: str, label: str) -> None:
    """校验字符串不为空。

    Args:
        value: 待校验字符串。
        label: 错误信息中的字段名。

    Returns:
        None。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")