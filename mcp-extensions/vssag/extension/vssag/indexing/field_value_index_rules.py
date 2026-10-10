"""数据库字段值索引的统计、评分和安全筛选规则。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from ..vssag_schemas.sql import (
    SQLCollectedFieldValue,
    SQLColumnInfo,
    SQLFieldIndexStatistics,
    SQLFieldValue,
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^(?:https?|ftp)://", re.IGNORECASE)
_IP_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")
_HASH_RE = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$", re.IGNORECASE)
_DATE_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$"
)
_BUSINESS_WORDS = {
    "品牌", "状态", "类型", "城市", "部门", "等级", "类别", "地区", "行业",
    "产品", "性别", "订单", "阶段", "brand", "status", "type", "city",
    "department", "level", "category", "region", "sector", "product",
    "gender", "order", "stage", "name", "kind",
}
_SENSITIVE_COLUMN_WORDS = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "access_key", "private_key", "credential", "ssn", "social_security",
    "phone", "mobile", "email",
}


def normalize_field_value(value: Any) -> str:
    """归一化用于规则比较的字段值，不修改主表原始值。

    Args:
        value: 数据库读取的原始字段值。

    Returns:
        完成 Unicode、空白和大小写归一化的比较文本。
    """

    text = unicodedata.normalize("NFKC", str(value))
    return " ".join(text.split()).casefold()


def classify_field_value(value: str) -> str:
    """识别字段值的内容类型。

    Args:
        value: 待判断的数据库字段值。

    Returns:
        内容类型名称。
    """

    text = value.strip()
    if not text:
        return "empty"
    if text.isdigit():
        return "number"
    if _UUID_RE.fullmatch(text):
        return "uuid"
    if _EMAIL_RE.fullmatch(text):
        return "email"
    if _URL_RE.match(text):
        return "url"
    if _IP_RE.fullmatch(text):
        return "ip"
    if _PATH_RE.match(text):
        return "path"
    if _HASH_RE.fullmatch(text):
        return "hash"
    if _DATE_RE.fullmatch(text):
        return "datetime"
    if re.fullmatch(r"[A-Za-z0-9_-]+", text) and len(text) >= 12:
        if len(set(text)) / len(text) >= 0.65:
            return "high_entropy"
    if re.search(r"[\u4e00-\u9fff]", text) or re.search(r"[A-Za-z]{3,}", text):
        return "natural_language"
    return "mixed"


def calculate_field_index_statistics(
    *,
    collection_job_id: str,
    database_id: int,
    db_type: str,
    schema_name: str,
    table_name: str,
    column: SQLColumnInfo,
    row_count: int | None,
    non_null_count: int,
    distinct_count: int,
    lengths: Sequence[int],
    pattern_distribution: Mapping[str, float],
    threshold: float,
    uncertain_min: float,
) -> SQLFieldIndexStatistics:
    """计算字段统计信息和自动索引判断结果。

    Args:
        collection_job_id: 当前字段值采集任务 ID。
        database_id: 目标数据库 ID。
        db_type: 目标数据库类型。
        schema_name: 数据库 Schema 名称。
        table_name: 原始表名。
        column: 待判断列信息。
        row_count: 表总行数。
        non_null_count: 字段非空行数。
        distinct_count: 字段不同值数量。
        lengths: 样本字段值长度。
        pattern_distribution: 样本内容类型占比。
        threshold: 自动采集最低得分。
        uncertain_min: 进入 LLM 标注的最低得分。

    Returns:
        字段统计信息和采集判断。
    """

    denominator = max(1, int(row_count or non_null_count or 1))
    distinct_ratio = min(1.0, distinct_count / denominator)
    null_ratio = max(0.0, 1.0 - (non_null_count / denominator))
    avg_length = sum(lengths) / len(lengths) if lengths else 0.0

    normalized_type = column.data_type.lower()
    if "enum" in normalized_type or "set" in normalized_type:
        type_score = 20.0
    elif any(item in normalized_type for item in ("varchar", "char", "character")):
        type_score = 15.0
    elif "text" in normalized_type:
        type_score = 10.0
    else:
        type_score = 5.0

    name_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", column.name)
    }
    comment_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", column.comment)
    }
    name_hits = sum(
        1
        for token in name_tokens
        if token in _BUSINESS_WORDS or any(word in token for word in _BUSINESS_WORDS)
    )
    comment_hits = sum(
        1
        for token in comment_tokens
        if token in _BUSINESS_WORDS or any(word in token for word in _BUSINESS_WORDS)
    )
    if comment_hits and name_hits:
        semantic_name_score = 20.0
    elif comment_hits:
        semantic_name_score = min(20.0, 15.0 + comment_hits)
    elif name_hits:
        semantic_name_score = min(15.0, 10.0 + name_hits)
    else:
        semantic_name_score = 0.0

    if distinct_count < 3 or distinct_count > 10000:
        cardinality_score = 0.0
    elif distinct_count <= 50:
        cardinality_score = 25.0
    elif distinct_count <= 500:
        cardinality_score = 25.0 - ((distinct_count - 50) / 450.0) * 10.0
    elif distinct_count <= 2000:
        cardinality_score = 15.0 - ((distinct_count - 500) / 1500.0) * 5.0
    else:
        cardinality_score = 10.0 - ((distinct_count - 2000) / 8000.0) * 5.0
    if distinct_ratio < 0.5:
        cardinality_score = max(0.0, cardinality_score - 5.0)

    if null_ratio < 0.05:
        quality_score = 15.0
    elif null_ratio <= 0.2:
        quality_score = 12.0
    elif null_ratio <= 0.5:
        quality_score = 8.0
    else:
        quality_score = 3.0
    if avg_length > 200:
        quality_score *= 0.3
    elif avg_length > 50:
        quality_score *= 0.7
    if pattern_distribution.get("natural_language", 0.0) > 0.5:
        quality_score *= 1.2
    if (
        pattern_distribution.get("uuid", 0.0)
        + pattern_distribution.get("hash", 0.0)
        + pattern_distribution.get("high_entropy", 0.0)
        > 0.5
    ):
        quality_score *= 0.3
    elif pattern_distribution.get("mixed", 0.0) > 0.5:
        quality_score *= 0.5
    quality_score = min(15.0, quality_score)

    field_score = min(
        100.0,
        type_score
        + semantic_name_score
        + cardinality_score
        + quality_score,
    )
    if field_score >= threshold:
        selection_decision = "candidate"
        rejection_reason = ""
    elif field_score >= uncertain_min:
        selection_decision = "uncertain"
        rejection_reason = "field_score_in_uncertain_range"
    else:
        selection_decision = "rejected"
        rejection_reason = "field_score_below_threshold"

    return SQLFieldIndexStatistics(
        collection_job_id=collection_job_id,
        database_id=database_id,
        db_type=db_type,
        schema_name=schema_name,
        raw_table_name=table_name,
        column_name=column.name,
        data_type=column.data_type,
        comment=column.comment,
        row_count=row_count,
        non_null_count=non_null_count,
        distinct_count=distinct_count,
        distinct_ratio=distinct_ratio,
        null_ratio=null_ratio,
        avg_length=avg_length,
        max_length=max(lengths, default=0),
        pattern_distribution=dict(pattern_distribution),
        type_score=type_score,
        semantic_name_score=semantic_name_score,
        cardinality_score=cardinality_score,
        quality_score=quality_score,
        usage_score=0.0,
        field_score=field_score,
        selection_decision=selection_decision,
        rejection_reason=rejection_reason,
    )


def get_field_value_rejection_reason(
    value: str,
    *,
    max_length: int,
    hard_reject_sensitive: bool,
    patterns: Sequence[str] = (),
    column_name: str = "",
) -> str:
    """返回字段值不能进入索引的原因。

    Args:
        value: 数据库字段原始值。
        max_length: 允许进入普通字段值索引的最大长度。
        hard_reject_sensitive: 是否启用敏感数据硬拒绝。
        patterns: 配置追加的拒绝正则。
        column_name: 字段名，用于敏感字段判断。

    Returns:
        拒绝原因；空字符串表示允许。

    Raises:
        re.error: 配置的正则表达式无效。
    """

    text = value.strip()
    if hard_reject_sensitive:
        normalized_column = column_name.casefold().replace("-", "_")
        if any(word in normalized_column for word in _SENSITIVE_COLUMN_WORDS):
            return "sensitive_column"
    if not text:
        return "empty"
    if len(text) > max_length:
        return "too_long"
    value_type = classify_field_value(text)
    if value_type in {
        "number", "uuid", "url", "email", "ip", "path", "hash", "datetime"
    }:
        return value_type
    if hard_reject_sensitive and value_type == "high_entropy":
        return "high_entropy"
    if patterns and any(re.search(pattern, text) for pattern in patterns):
        return "configured_pattern"
    return ""


def build_collected_field_value(
    *,
    raw_value: Any,
    value_count: int,
    database_id: int,
    table: Any,
    column: SQLColumnInfo,
    max_length: int,
    hard_reject_sensitive: bool,
    patterns: Sequence[str],
) -> SQLCollectedFieldValue | None:
    """构建不包含运行状态字段的主表记录和采集计数。

    Args:
        raw_value: 数据库字段原始值。
        value_count: 原始数据库中该值的引用数量。
        database_id: 目标数据库 ID。
        table: 字段所属表结构。
        column: 字段定义。
        max_length: 允许进入索引的最大长度。
        hard_reject_sensitive: 是否启用敏感数据硬拒绝。
        patterns: 配置追加的拒绝正则。

    Returns:
        可入库的字段值采集结果；被拒绝时返回空值。
    """

    raw_text = str(raw_value)
    if get_field_value_rejection_reason(
        raw_text,
        max_length=max_length,
        hard_reject_sensitive=hard_reject_sensitive,
        patterns=patterns,
        column_name=column.name,
    ):
        return None
    return SQLCollectedFieldValue(
        value=SQLFieldValue(
            database_id=database_id,
            schema_name=table.raw_schema_name,
            raw_value=raw_text,
            raw_table_name=table.raw_table_name,
            random_table_name=table.random_table_name,
            column_name=column.name,
            aliases=[],
            retrieval_text=raw_text,
        ),
        value_count=max(0, int(value_count)),
    )


def merge_collected_field_values(
    values: Sequence[SQLCollectedFieldValue],
) -> list[SQLCollectedFieldValue]:
    """合并三个数据库 Provider 可能产生的重复字段值结果。

    Args:
        values: 待合并的字段值采集结果。

    Returns:
        按主表唯一键合并后的字段值采集结果。
    """

    merged: dict[tuple[int, str, str, str, str], SQLCollectedFieldValue] = {}
    for item in values:
        value = item.value
        key = (
            value.database_id,
            value.schema_name,
            value.raw_table_name,
            value.column_name,
            value.raw_value,
        )
        current = merged.get(key)
        if current is None:
            merged[key] = item
        else:
            current.value_count += item.value_count
    return list(merged.values())
