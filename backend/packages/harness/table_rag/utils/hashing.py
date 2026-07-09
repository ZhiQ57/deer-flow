"""通用 hash 计算工具。"""

from __future__ import annotations

import hashlib


def stable_sha256_hex(value: str) -> str:
    """计算字符串的稳定 SHA-256 十六进制摘要。

    Args:
        value: 待计算摘要的字符串。

    Returns:
        SHA-256 十六进制字符串。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
