"""通用文本清洗和轻量归一化工具。"""

from __future__ import annotations

import re
import unicodedata


def simple_text_normalize(value: str) -> str:
    """对文本做轻量归一化。

    Args:
        value: 原始文本。

    Returns:
        去空格、去标点、统一全半角和大小写后的文本。
    """
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    return normalized