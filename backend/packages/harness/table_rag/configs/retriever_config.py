"""TableRAG 配置结构和配置文件加载。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .database_config import DatabaseConfig
from ..schemas import FieldValueFilter, FieldValueIndexTarget, TableValueIndexTarget


POSTGRES_DEFAULT_INDEX_SCHEMA = "public"
"""PostgreSQL 索引表默认 schema。

当前 SDK 配置不再暴露 PostgreSQL 专用的 schema_name 字段，避免和 Text2SQL 语义中的
schema / 表结构混淆。PostgreSQL 实现统一使用 public，后续如确实需要多 schema 隔离，
应作为明确的 PostgreSQL 专项能力单独设计。
"""


@dataclass(frozen=True)
class IndexTableSettings:
    """TableRAG 自维护索引表名配置。"""

    evidence: str = "nl2sql_evidence_index"
    evidence_embeddings: str = "nl2sql_evidence_embedding"
    table_schema: str = "nl2sql_table_index"
    column_schema: str = "nl2sql_column_index"
    join_edges: str = "nl2sql_schema_join_edges"
    field_values: str = "nl2sql_field_value_index"
    field_value_embeddings: str = "nl2sql_field_value_embedding"

    def __post_init__(self) -> None:
        """校验索引表名配置。"""
        _require_safe_identifier(self.evidence, "index_store.tables.evidence")
        _require_safe_identifier(self.evidence_embeddings, "index_store.tables.evidence_embeddings")
        _require_safe_identifier(self.table_schema, "index_store.tables.table_schema")
        _require_safe_identifier(self.column_schema, "index_store.tables.column_schema")
        _require_safe_identifier(self.join_edges, "index_store.tables.join_edges")
        _require_safe_identifier(self.field_values, "index_store.tables.field_values")
        _require_safe_identifier(self.field_value_embeddings, "index_store.tables.field_value_embeddings")


@dataclass(frozen=True)
class PostgresIndexOptions:
    """PostgreSQL 索引存储选项。"""

    enable_bm25: bool = True
    enable_pg_trgm: bool = True


@dataclass(frozen=True)
class EmbeddingTargetSettings:
    """单个索引对象的向量索引配置。"""

    enabled: bool = False
    dimension: int | None = None

    def __post_init__(self) -> None:
        """校验单个索引对象的向量参数。"""
        if not isinstance(self.enabled, bool):
            raise TypeError("embedding target enabled must be a boolean")
        if self.dimension is not None and (
            not isinstance(self.dimension, int) or isinstance(self.dimension, bool) or self.dimension <= 0
        ):
            raise ValueError("embedding target dimension must be a positive integer or null")


@dataclass(frozen=True)
class EmbeddingIndexSettings:
    """索引表向量字段和外部向量写入参数。"""

    default_dimension: int = 768
    batch_size: int = 128
    cache_size: int = 10000
    table: EmbeddingTargetSettings = field(default_factory=EmbeddingTargetSettings)
    column: EmbeddingTargetSettings = field(default_factory=EmbeddingTargetSettings)
    evidence: EmbeddingTargetSettings = field(default_factory=EmbeddingTargetSettings)
    field_value: EmbeddingTargetSettings = field(default_factory=EmbeddingTargetSettings)

    def __post_init__(self) -> None:
        """校验 embedding 索引参数。"""
        if (
            not isinstance(self.default_dimension, int)
            or isinstance(self.default_dimension, bool)
            or self.default_dimension <= 0
        ):
            raise ValueError("index_store.embedding.default_dimension must be a positive integer")
        if (
            not isinstance(self.batch_size, int)
            or isinstance(self.batch_size, bool)
            or self.batch_size <= 0
        ):
            raise ValueError("index_store.embedding.batch_size must be a positive integer")
        if (
            not isinstance(self.cache_size, int)
            or isinstance(self.cache_size, bool)
            or self.cache_size < 0
        ):
            raise ValueError("index_store.embedding.cache_size must be >= 0")

    @property
    def requires_pgvector(self) -> bool:
        """返回是否有任意索引对象启用 pgvector。"""
        return any(
            (
                self.table.enabled,
                self.column.enabled,
                self.evidence.enabled,
                self.field_value.enabled,
            )
        )

    def resolved_dimension(self, target: EmbeddingTargetSettings) -> int:
        """返回某个索引对象最终使用的向量维度。

        Args:
            target: 单个索引对象的向量配置。

        Returns:
            解析后的向量维度。
        """
        return target.dimension or self.default_dimension


@dataclass(frozen=True)
class IndexStoreSettings:
    """索引存储配置，统一承载表、列、Join Graph 和字段值索引表设置。"""

    tables: IndexTableSettings = field(default_factory=IndexTableSettings)
    postgres: PostgresIndexOptions = field(default_factory=PostgresIndexOptions)
    embedding: EmbeddingIndexSettings = field(default_factory=EmbeddingIndexSettings)

    @property
    def schema_name(self) -> str:
        """返回 PostgreSQL 默认索引 schema。"""
        return POSTGRES_DEFAULT_INDEX_SCHEMA

    @property
    def table_index_name(self) -> str:
        """返回表结构索引表名。"""
        return self.tables.table_schema

    @property
    def evidence_index_name(self) -> str:
        """返回 Evidence 证据索引表名。"""
        return self.tables.evidence

    @property
    def evidence_embedding_table_name(self) -> str:
        """返回 Evidence 可选向量索引表名。"""
        return self._embedding_table_name(self.tables.evidence_embeddings, self.evidence_embedding_dimension)

    @property
    def column_index_name(self) -> str:
        """返回列字段索引表名。"""
        return self.tables.column_schema

    @property
    def join_edge_table_name(self) -> str:
        """返回 Join Graph 边索引表名。"""
        return self.tables.join_edges

    @property
    def field_value_table_name(self) -> str:
        """返回字段值索引表名。"""
        return self.tables.field_values

    @property
    def field_value_embedding_table_name(self) -> str:
        """返回字段值可选向量索引表名。"""
        return self._embedding_table_name(self.tables.field_value_embeddings, self.field_value_embedding_dimension)

    @property
    def enable_pg_trgm(self) -> bool:
        """返回是否启用 pg_trgm。"""
        return self.postgres.enable_pg_trgm

    @property
    def enable_bm25(self) -> bool:
        """返回是否启用 pg_search BM25。"""
        return self.postgres.enable_bm25

    @property
    def requires_pgvector(self) -> bool:
        """返回当前索引结构是否需要 pgvector 扩展。"""
        return self.embedding.requires_pgvector

    @property
    def table_embedding_enabled(self) -> bool:
        """返回表结构索引是否启用向量字段。"""
        return self.embedding.table.enabled

    @property
    def column_embedding_enabled(self) -> bool:
        """返回列字段索引是否启用向量字段。"""
        return self.embedding.column.enabled

    @property
    def evidence_embedding_enabled(self) -> bool:
        """返回 Evidence 是否启用独立向量表。"""
        return self.embedding.evidence.enabled

    @property
    def field_value_embedding_enabled(self) -> bool:
        """返回字段值索引是否启用独立向量表。"""
        return self.embedding.field_value.enabled

    @property
    def table_embedding_dimension(self) -> int:
        """返回表结构索引向量维度。"""
        return self.embedding.resolved_dimension(self.embedding.table)

    @property
    def column_embedding_dimension(self) -> int:
        """返回列字段索引向量维度。"""
        return self.embedding.resolved_dimension(self.embedding.column)

    @property
    def evidence_embedding_dimension(self) -> int:
        """返回 Evidence 向量表维度。"""
        return self.embedding.resolved_dimension(self.embedding.evidence)

    @property
    def field_value_embedding_dimension(self) -> int:
        """返回字段值向量表维度。"""
        return self.embedding.resolved_dimension(self.embedding.field_value)

    def _embedding_table_name(self, base_name: str, dimension: int) -> str:
        """根据向量维度生成向量表名。"""
        return f"{base_name}_{dimension}"


@dataclass(frozen=True)
class EvidenceRetrievalSettings:
    """业务规则和证据在线召回权重配置。"""

    bm25_weight: float = 0.55
    fuzzy_weight: float = 0.15
    vector_weight: float = 0.0

    def __post_init__(self) -> None:
        """校验证据召回权重。"""
        _require_non_negative_number(self.bm25_weight, "retrieval.evidence.bm25_weight")
        _require_non_negative_number(self.fuzzy_weight, "retrieval.evidence.fuzzy_weight")
        _require_non_negative_number(self.vector_weight, "retrieval.evidence.vector_weight")


@dataclass(frozen=True)
class TableRetrievalSettings:
    """表结构在线召回权重配置。"""

    bm25_weight: float = 0.55
    fuzzy_weight: float = 0.15
    vector_weight: float = 0.0

    def __post_init__(self) -> None:
        """校验表召回权重。"""
        _require_non_negative_number(self.bm25_weight, "retrieval.table.bm25_weight")
        _require_non_negative_number(self.fuzzy_weight, "retrieval.table.fuzzy_weight")
        _require_non_negative_number(self.vector_weight, "retrieval.table.vector_weight")


@dataclass(frozen=True)
class ColumnRetrievalSettings:
    """列字段在线召回权重配置。"""

    bm25_weight: float = 0.55
    fuzzy_weight: float = 0.15
    vector_weight: float = 0.0

    def __post_init__(self) -> None:
        """校验列召回权重。"""
        _require_non_negative_number(self.bm25_weight, "retrieval.column.bm25_weight")
        _require_non_negative_number(self.fuzzy_weight, "retrieval.column.fuzzy_weight")
        _require_non_negative_number(self.vector_weight, "retrieval.column.vector_weight")


@dataclass(frozen=True)
class HybridRetrievalSettings:
    """混合检索融合配置。"""

    column_table_boost: float = 0.25
    value_table_boost: float = 0.25

    def __post_init__(self) -> None:
        """校验混合融合权重。"""
        _require_non_negative_number(self.column_table_boost, "retrieval.hybrid.column_table_boost")
        _require_non_negative_number(self.value_table_boost, "retrieval.hybrid.value_table_boost")


@dataclass(frozen=True)
class JoinGraphRetrievalSettings:
    """Join Graph 在线召回配置。"""

    max_edges: int = 5000

    def __post_init__(self) -> None:
        """校验 Join Graph 配置。"""
        if self.max_edges <= 0:
            raise ValueError("retrieval.join_graph.max_edges must be a positive integer")


@dataclass(frozen=True)
class RerankerWeightSettings:
    """重排序特征权重配置。"""

    entity_boost: float = 0.12
    exact_alias_hit: float = 0.2
    metric_role_match: float = 0.2
    dimension_role_match: float = 0.15
    business_domain_prior: float = 0.1

    def __post_init__(self) -> None:
        """校验重排序特征权重。"""
        _require_non_negative_number(self.entity_boost, "retrieval.reranker.weights.entity_boost")
        _require_non_negative_number(self.exact_alias_hit, "retrieval.reranker.weights.exact_alias_hit")
        _require_non_negative_number(self.metric_role_match, "retrieval.reranker.weights.metric_role_match")
        _require_non_negative_number(self.dimension_role_match, "retrieval.reranker.weights.dimension_role_match")
        _require_non_negative_number(self.business_domain_prior, "retrieval.reranker.weights.business_domain_prior")


@dataclass(frozen=True)
class RerankerSettings:
    """重排序器配置。"""

    enabled: bool = True
    type: str = "weighted_schema"
    rrf_k: int = 60
    weights: RerankerWeightSettings = field(default_factory=RerankerWeightSettings)

    def __post_init__(self) -> None:
        """校验重排序器类型。"""
        normalized_type = str(self.type).strip().lower()
        supported_types = {"weighted_schema", "rrf"}
        if normalized_type not in supported_types:
            raise ValueError(
                "retrieval.reranker.type supports weighted_schema or rrf. "
                "External provider rerank is configured by injecting rerank_provider into runtime or retriever factory."
            )
        if not isinstance(self.rrf_k, int) or isinstance(self.rrf_k, bool) or self.rrf_k <= 0:
            raise ValueError("retrieval.reranker.rrf_k must be a positive integer")
        object.__setattr__(self, "type", normalized_type)


@dataclass(frozen=True)
class RetrievalSettings:
    """在线检索配置，承载表、列、混合召回、Join Graph 和 reranker 参数。"""

    evidence: EvidenceRetrievalSettings = field(default_factory=EvidenceRetrievalSettings)
    table: TableRetrievalSettings = field(default_factory=TableRetrievalSettings)
    column: ColumnRetrievalSettings = field(default_factory=ColumnRetrievalSettings)
    hybrid: HybridRetrievalSettings = field(default_factory=HybridRetrievalSettings)
    join_graph: JoinGraphRetrievalSettings = field(default_factory=JoinGraphRetrievalSettings)
    reranker: RerankerSettings = field(default_factory=RerankerSettings)


@dataclass(frozen=True)
class FieldValueSyncSettings:
    """字段值同步配置，声明定时抽取哪些业务表字段并写入字段值索引。"""

    interval_seconds: int = 3600
    batch_size: int = 1000
    targets: list[TableValueIndexTarget] = field(default_factory=list)

    def __post_init__(self) -> None:
        """校验字段值同步配置。"""
        if self.interval_seconds <= 0:
            raise ValueError("field_value_sync.interval_seconds must be a positive integer")
        if self.batch_size <= 0:
            raise ValueError("field_value_sync.batch_size must be a positive integer")


@dataclass(frozen=True)
class ValueIndexSearchOptions:
    """字段值检索选项，用于控制返回数量、表字段过滤和最低分。"""

    limit: int = 20
    table_names: list[str] | None = None
    column_names: list[str] | None = None
    min_score: float | None = None

    def __post_init__(self) -> None:
        """校验字段值检索选项。"""
        if self.limit <= 0:
            raise ValueError("value index search limit must be a positive integer")
        _require_optional_score(self.min_score, "value index search min_score")
        _require_optional_string_list(self.table_names, "value index search table_names")
        _require_optional_string_list(self.column_names, "value index search column_names")


@dataclass(frozen=True)
class TableRAGConfig:
    """TableRAG 总配置，聚合索引存储、在线检索和字段值同步设置。"""

    database_type: str = "postgresql"
    index_store: IndexStoreSettings = field(default_factory=IndexStoreSettings)
    retrieval: RetrievalSettings = field(default_factory=RetrievalSettings)
    field_value_sync: FieldValueSyncSettings = field(default_factory=FieldValueSyncSettings)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    @classmethod
    def from_file(cls, path: str | Path) -> "TableRAGConfig":
        """从 YAML 或 JSON 文件加载配置。

        Args:
            path: 配置文件路径。

        Returns:
            TableRAG 总配置。
        """
        return cls.from_dict(load_config_mapping(path))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TableRAGConfig":
        """从字典构造配置对象，只接受新版配置结构。

        Args:
            data: 外部 YAML 或 JSON 反序列化后的配置字典。

        Returns:
            TableRAG 总配置。
        """
        _reject_legacy_config_keys(data)
        return cls(
            database_type=_database_type_from_dict(data),
            index_store=_index_store_from_dict(data.get("index_store", {})),
            retrieval=_retrieval_from_dict(data.get("retrieval", {})),
            field_value_sync=_field_value_sync_from_dict(data.get("field_value_sync", {})),
            database=DatabaseConfig.from_dict(data),
        )


def load_config_mapping(path: str | Path) -> dict[str, Any]:
    """读取 YAML 或 JSON 配置文件。

    Args:
        path: 配置文件路径。

    Returns:
        配置字典。
    """
    config_path = Path(path)
    suffix = config_path.suffix.lower()
    text = config_path.read_text(encoding="utf-8")
    if suffix == ".json":
        return json.loads(text)
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("YAML config requires PyYAML. Install with: pip install 'PyYAML>=6.0'") from exc
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file must contain a mapping: {config_path}")
        return loaded
    raise ValueError(f"Unsupported config suffix: {config_path.suffix}. Use .json, .yaml, or .yml")


def _reject_legacy_config_keys(data: dict[str, Any]) -> None:
    """拒绝旧版配置字段，避免重构后静默兼容导致误配置。

    Args:
        data: 配置字典。

    Returns:
        None。
    """
    legacy_keys = {"schema_recall", "value_index", "value_targets", "targets", "index"}
    found = sorted(key for key in legacy_keys if key in data)
    if found:
        raise ValueError(
            "TableRAG config no longer supports legacy keys: "
            + ", ".join(found)
            + ". Use index_store, retrieval and field_value_sync instead."
        )


def _database_type_from_dict(data: dict[str, Any]) -> str:
    """从配置字典中读取检索数据库类型。

    Args:
        data: 配置字典。

    Returns:
        检索数据库类型。
    """
    retriever_data = data.get("retriever")
    if isinstance(retriever_data, dict) and retriever_data.get("database_type"):
        return str(retriever_data["database_type"])
    return str(data.get("database_type", data.get("retriever_backend", "postgresql")))


def _index_store_from_dict(data: Any) -> IndexStoreSettings:
    """解析 index_store 配置。

    Args:
        data: index_store 配置字典。

    Returns:
        索引存储配置。
    """
    mapping = _as_mapping(data, "index_store")
    if "schema_name" in mapping or "index_schema" in mapping or "postgres_schema" in mapping:
        raise ValueError("index_store no longer supports schema_name/index_schema/postgres_schema; PostgreSQL uses public")
    postgres_mapping = _as_mapping(mapping.get("postgres", {}), "index_store.postgres")
    if "text_search_config" in postgres_mapping:
        raise ValueError("index_store.postgres.text_search_config has been removed. PostgreSQL retrieval uses pg_search BM25.")
    if "enable_pgvector" in postgres_mapping:
        raise ValueError(
            "index_store.postgres.enable_pgvector has been removed. "
            "Use index_store.embedding.<table|column|evidence|field_value>.enabled instead."
        )
    return IndexStoreSettings(
        tables=IndexTableSettings(**_as_mapping(mapping.get("tables", {}), "index_store.tables")),
        postgres=PostgresIndexOptions(**postgres_mapping),
        embedding=_embedding_settings_from_dict(mapping.get("embedding", {})),
    )


def _embedding_settings_from_dict(data: Any) -> EmbeddingIndexSettings:
    """解析 embedding 索引配置。

    Args:
        data: embedding 配置片段。

    Returns:
        embedding 索引配置。
    """
    mapping = _as_mapping(data, "index_store.embedding")
    if "dimension" in mapping:
        raise ValueError(
            "index_store.embedding.dimension has been removed. "
            "Use index_store.embedding.default_dimension or per-target dimension instead."
        )
    clean = dict(mapping)
    for target_name in ("table", "column", "evidence", "field_value"):
        clean[target_name] = _embedding_target_from_dict(clean.get(target_name, {}), target_name)
    return EmbeddingIndexSettings(**clean)


def _embedding_target_from_dict(data: Any, target_name: str) -> EmbeddingTargetSettings:
    """解析单个索引对象的 embedding 配置。

    Args:
        data: 单个索引对象配置。
        target_name: 索引对象名称。

    Returns:
        单个索引对象的 embedding 配置。
    """
    return EmbeddingTargetSettings(**_as_mapping(data, f"index_store.embedding.{target_name}"))


def _retrieval_from_dict(data: Any) -> RetrievalSettings:
    """解析 retrieval 配置。

    Args:
        data: retrieval 配置字典。

    Returns:
        在线检索配置。
    """
    mapping = _as_mapping(data, "retrieval")
    reranker_mapping = _as_mapping(mapping.get("reranker", {}), "retrieval.reranker")
    weights_mapping = _as_mapping(reranker_mapping.get("weights", {}), "retrieval.reranker.weights")
    table_mapping = _as_mapping(mapping.get("table", {}), "retrieval.table")
    column_mapping = _as_mapping(mapping.get("column", {}), "retrieval.column")
    if "lexical_weight" in table_mapping:
        raise ValueError("retrieval.table.lexical_weight has been removed. Use retrieval.table.fuzzy_weight.")
    if "lexical_weight" in column_mapping:
        raise ValueError("retrieval.column.lexical_weight has been removed. Use retrieval.column.fuzzy_weight.")
    return RetrievalSettings(
        evidence=EvidenceRetrievalSettings(**_as_mapping(mapping.get("evidence", {}), "retrieval.evidence")),
        table=TableRetrievalSettings(**table_mapping),
        column=ColumnRetrievalSettings(**column_mapping),
        hybrid=HybridRetrievalSettings(**_as_mapping(mapping.get("hybrid", {}), "retrieval.hybrid")),
        join_graph=JoinGraphRetrievalSettings(**_as_mapping(mapping.get("join_graph", {}), "retrieval.join_graph")),
        reranker=RerankerSettings(
            enabled=bool(reranker_mapping.get("enabled", True)),
            type=str(reranker_mapping.get("type", "weighted_schema")),
            rrf_k=reranker_mapping.get("rrf_k", 60),
            weights=RerankerWeightSettings(**weights_mapping),
        ),
    )


def _field_value_sync_from_dict(data: Any) -> FieldValueSyncSettings:
    """解析 field_value_sync 配置。

    Args:
        data: field_value_sync 配置字典。

    Returns:
        字段值同步配置。
    """
    mapping = _as_mapping(data, "field_value_sync")
    targets = [_table_value_target_from_dict(item) for item in mapping.get("targets", [])]
    clean = dict(mapping)
    clean["targets"] = targets
    return FieldValueSyncSettings(**clean)


def _as_mapping(data: Any, label: str) -> dict[str, Any]:
    """校验配置片段是否为字典。

    Args:
        data: 配置片段。
        label: 错误信息中的配置路径。

    Returns:
        配置字典。
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a mapping")
    return data


def _table_value_target_from_dict(data: dict[str, Any]) -> TableValueIndexTarget:
    """把单个字段值同步目标配置转换为 TableValueIndexTarget。

    Args:
        data: 字段值同步目标配置。

    Returns:
        标准字段值同步目标。
    """
    fields_data = data.get("fields", [])
    fields = [_field_value_target_from_config(item) for item in _normalize_field_items(fields_data)]
    clean = dict(data)
    clean["fields"] = fields
    clean["filters"] = [_field_value_filter_from_config(item) for item in _normalize_filter_items(clean.get("filters"))]
    if "where_clause" in clean:
        legacy_where_clause = clean.pop("where_clause")
        if legacy_where_clause is not None:
            raise ValueError(
                "field_value_sync.targets[].where_clause has been removed. Use unsafe_where_clause explicitly."
            )
    source = clean.pop("source", None)
    if source is not None:
        source_mapping = _as_mapping(source, "field_value_sync.targets[].source")
        clean["source_schema"] = source_mapping.get("schema", POSTGRES_DEFAULT_INDEX_SCHEMA)
        clean["source_table"] = source_mapping.get("table")
    return TableValueIndexTarget(**clean)


def _normalize_field_items(value: Any) -> list[Any]:
    """把字段配置统一转换成列表。

    Args:
        value: 字段配置，可为列表或空值。

    Returns:
        字段配置列表。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ValueError("field_value_sync.targets[].fields must be a list")


def _normalize_filter_items(value: Any) -> list[Any]:
    """把安全过滤条件配置统一转换成列表。

    Args:
        value: 过滤条件配置，可为列表或空值。

    Returns:
        过滤条件配置列表。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ValueError("field_value_sync.targets[].filters must be a list")


def _field_value_target_from_config(item: Any) -> FieldValueIndexTarget:
    """把单个字段配置转换为 FieldValueIndexTarget。

    Args:
        item: 字段配置项。

    Returns:
        字段值索引目标字段。
    """
    if not isinstance(item, dict):
        raise ValueError(f"field item must be a mapping, got: {type(item).__name__}")
    if "aliases" in item:
        raise ValueError(
            "Do not configure raw-value aliases in the field value sync target. "
            "Configure only the tables/columns to sync; provide aliases through a pluggable AliasProvider."
        )
    return FieldValueIndexTarget(**item)


def _field_value_filter_from_config(item: Any) -> FieldValueFilter:
    """把单个过滤配置转换为 FieldValueFilter。

    Args:
        item: 过滤配置项。

    Returns:
        字段值同步安全过滤条件。
    """
    if not isinstance(item, dict):
        raise ValueError(f"filter item must be a mapping, got: {type(item).__name__}")
    return FieldValueFilter(**item)


def _require_safe_identifier(value: str, label: str) -> None:
    """校验配置中的安全标识符。

    Args:
        value: 待校验字符串。
        label: 错误信息字段名。

    Returns:
        None。
    """
    import re

    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"{label} must be a safe identifier")


def _require_non_negative_number(value: float, label: str) -> None:
    """校验非负有限数值。

    Args:
        value: 待校验数值。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric")
    if value != value or value in {float("inf"), float("-inf")} or value < 0:
        raise ValueError(f"{label} must be a non-negative finite number")


def _require_optional_score(value: float | None, label: str) -> None:
    """校验可选分数阈值。

    Args:
        value: 待校验分数。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if value is not None:
        _require_non_negative_number(value, label)


def _require_optional_string_list(value: list[str] | None, label: str) -> None:
    """校验可选字符串列表。

    Args:
        value: 待校验列表。
        label: 错误信息字段名。

    Returns:
        None。
    """
    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label}[{index}] must be a non-empty string")
