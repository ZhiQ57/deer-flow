"""表和列 schema 检索表征构建。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ....utils.validation import validate_safe_identifier


@dataclass(frozen=True)
class TableSchemaRepresentation:
    """表级 schema 检索表征。"""

    table_name: str
    table_label: str | None = None
    table_description: str | None = None
    business_domain: str | None = None
    grain: str | None = None
    table_type: str | None = None
    entities: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    common_questions: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    search_text: str = ""

    def to_index_metadata(self) -> dict[str, Any]:
        """生成适合写入表索引 metadata JSONB 的结构。"""
        return _drop_empty(
            {
                **self.metadata,
                "domain": self.business_domain,
                "grain": self.grain,
                "table_type": self.table_type,
                "aliases": self.aliases,
                "common_questions": self.common_questions,
                "metrics": self.metrics,
                "dimensions": self.dimensions,
                "schema_search_text": self.search_text,
            }
        )


@dataclass(frozen=True)
class ColumnSchemaRepresentation:
    """列级 schema 检索表征。"""

    table_name: str
    column_name: str
    column_comment: str | None = None
    data_type: str | None = None
    role: str | None = None
    business_type: str | None = None
    aliases: list[str] = field(default_factory=list)
    example_values: list[str] = field(default_factory=list)
    default_aggregation: str | None = None
    is_foreign_key: bool = False
    references: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    search_text: str = ""

    def to_index_metadata(self) -> dict[str, Any]:
        """生成适合写入列索引 metadata JSONB 的结构。"""
        return _drop_empty(
            {
                **self.metadata,
                "data_type": self.data_type,
                "role": self.role,
                "business_type": self.business_type,
                "aliases": self.aliases,
                "example_values": self.example_values,
                "default_aggregation": self.default_aggregation,
                "is_foreign_key": self.is_foreign_key,
                "references": self.references,
                "schema_search_text": self.search_text,
            }
        )


def build_table_schema_representation(
    *,
    table_name: str,
    table_label: str | None = None,
    table_description: str | None = None,
    entities: list[str] | tuple[str, ...] | None = None,
    metadata: dict[str, Any] | None = None,
) -> TableSchemaRepresentation:
    """构建表级专业 schema 检索表征。

    Args:
        table_name: 物理或逻辑表名。
        table_label: 中文或业务表名。
        table_description: 表业务定义。
        entities: 表级实体词、业务别名或关键词。
        metadata: 外部传入的表级 metadata。

    Returns:
        表级 schema 表征。
    """
    validate_safe_identifier(table_name, "table_name")
    clean_metadata = dict(metadata or {})
    aliases = _string_list(clean_metadata.get("aliases"))
    common_questions = _string_list(clean_metadata.get("common_questions"))
    metrics = _string_list(clean_metadata.get("metrics"))
    dimensions = _string_list(clean_metadata.get("dimensions"))
    representation = TableSchemaRepresentation(
        table_name=table_name,
        table_label=table_label,
        table_description=table_description,
        business_domain=_string_or_none(clean_metadata.get("domain") or clean_metadata.get("business_domain")),
        grain=_string_or_none(clean_metadata.get("grain")),
        table_type=_string_or_none(clean_metadata.get("table_type")),
        entities=_dedupe([*(entities or ()), *aliases]),
        aliases=aliases,
        common_questions=common_questions,
        metrics=metrics,
        dimensions=dimensions,
        metadata=clean_metadata,
    )
    return _with_table_search_text(representation)


def build_column_schema_representation(
    *,
    table_name: str,
    column_name: str,
    column_comment: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ColumnSchemaRepresentation:
    """构建列级专业 schema 检索表征。

    Args:
        table_name: 列所属表名。
        column_name: 列名。
        column_comment: 中文字段说明。
        metadata: 外部传入的列级 metadata。

    Returns:
        列级 schema 表征。
    """
    validate_safe_identifier(table_name, "table_name")
    validate_safe_identifier(column_name, "column_name")
    clean_metadata = dict(metadata or {})
    aliases = _string_list(clean_metadata.get("aliases"))
    representation = ColumnSchemaRepresentation(
        table_name=table_name,
        column_name=column_name,
        column_comment=column_comment,
        data_type=_string_or_none(clean_metadata.get("data_type")),
        role=_string_or_none(clean_metadata.get("role")),
        business_type=_string_or_none(clean_metadata.get("business_type") or clean_metadata.get("role")),
        aliases=aliases,
        example_values=_string_list(clean_metadata.get("example_values")),
        default_aggregation=_string_or_none(clean_metadata.get("default_aggregation")),
        is_foreign_key=bool(clean_metadata.get("is_foreign_key")),
        references=_string_or_none(clean_metadata.get("references")),
        metadata=clean_metadata,
    )
    return _with_column_search_text(representation)


def _with_table_search_text(representation: TableSchemaRepresentation) -> TableSchemaRepresentation:
    """补全表级检索文本。"""
    parts = [
        ("表名", representation.table_name),
        ("业务名", representation.table_label),
        ("定义", representation.table_description),
        ("业务域", representation.business_domain),
        ("粒度", representation.grain),
        ("类型", representation.table_type),
        ("实体", representation.entities),
        ("别名", representation.aliases),
        ("常见问法", representation.common_questions),
        ("指标", representation.metrics),
        ("维度", representation.dimensions),
    ]
    return TableSchemaRepresentation(
        **{
            **representation.__dict__,
            "search_text": _join_labeled_parts(parts),
        }
    )


def _with_column_search_text(representation: ColumnSchemaRepresentation) -> ColumnSchemaRepresentation:
    """补全列级检索文本。"""
    parts = [
        ("表名", representation.table_name),
        ("列名", representation.column_name),
        ("说明", representation.column_comment),
        ("数据类型", representation.data_type),
        ("字段角色", representation.role),
        ("业务类型", representation.business_type),
        ("聚合", representation.default_aggregation),
        ("别名", representation.aliases),
        ("样例值", representation.example_values),
        ("外键", representation.references if representation.is_foreign_key else None),
    ]
    return ColumnSchemaRepresentation(
        **{
            **representation.__dict__,
            "search_text": _join_labeled_parts(parts),
        }
    )


def _join_labeled_parts(parts: list[tuple[str, Any]]) -> str:
    """把结构化语义片段拼成稳定检索文本。"""
    chunks: list[str] = []
    for label, value in parts:
        values = _string_list(value)
        if not values:
            continue
        chunks.append(f"{label}: {' '.join(values)}")
    return " | ".join(chunks)


def _string_list(value: Any) -> list[str]:
    """把任意 metadata 值转换为去重字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        return _dedupe(f"{key}:{item}" for key, item in value.items() if item is not None and str(item))
    if isinstance(value, (list, tuple, set)):
        return _dedupe(str(item) for item in value if item is not None and str(item))
    return [str(value)] if str(value) else []


def _string_or_none(value: Any) -> str | None:
    """把 metadata 标量转换为字符串。"""
    values = _string_list(value)
    return values[0] if values else None


def _dedupe(values: Any) -> list[str]:
    """按输入顺序去重并过滤空白。"""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    """删除空值，保留 False 和 0 等有意义值。"""
    return {
        key: value
        for key, value in data.items()
        if value is not None and value != [] and value != {}
    }


__all__ = [
    "ColumnSchemaRepresentation",
    "TableSchemaRepresentation",
    "build_column_schema_representation",
    "build_table_schema_representation",
]
