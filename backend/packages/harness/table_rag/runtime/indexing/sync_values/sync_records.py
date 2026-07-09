"""字段值索引记录构造辅助函数。"""

from __future__ import annotations

from ....schemas import FieldValueRecord
from ....utils.text import simple_text_normalize


def record_search_text(record: FieldValueRecord) -> str:
    """拼接用于 BM25 检索的冗余文本。

    Args:
        record: 待写入索引表的字段值记录。

    Returns:
        由字段值、归一化值、别名、表字段语义拼接成的检索文本。
    """
    parts = [
        record.raw_value,
        record.normalized_value,
        *record.aliases,
        record.column_name,
        record.column_comment,
        record.table_name,
        record.table_comment,
    ]
    return " ".join(str(part).strip() for part in parts if part and str(part).strip())


def simple_query_normalize(value: str) -> str:
    """对用户查询做轻量归一化。

    Args:
        value: 用户原始查询文本。

    Returns:
        去空格、去标点、统一全半角和大小写后的查询文本。
    """
    return simple_text_normalize(value)


__all__ = [
    "record_search_text",
    "simple_query_normalize",
]
