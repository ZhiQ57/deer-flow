"""Hybrid SQL Schema retrieval over tables, values, evidence and graph edges."""

from __future__ import annotations

from ...config import SQLAbilityConfig
from ...errors import ConfigurationError
from ...indexing.field_value_index_rules import normalize_field_value
from ...indexing.index_stores.sql_schema_store import SQLSchemaStore
from ...vssag_schemas.sql import (
    SQLFieldValueBinding,
    SQLFieldValueSearchResult,
    SQLScope,
    SQLSearchHit,
    SQLSearchResponse,
)


class SQLSchemaRetriever:
    """执行 SQL Schema 混合检索并在响应边界完成脱敏投影。"""

    def __init__(self, store: SQLSchemaStore, config: SQLAbilityConfig, *, embedder=None):
        """初始化 SQL 检索器。

        Args:
            store: SQL Schema 索引存储。
            config: SQL 能力配置。
            embedder: 可选的向量模型替身。
        """

        self.store = store
        self.config = config
        embedding = config.retriever_service.index.embedding
        self.embedder = embedder
        if embedding.enabled and self.embedder is None:
            raise ConfigurationError(
                "SQL vector retrieval requires an injected Embedder"
            )

    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        include_values: bool = True,
        include_evidence: bool = True,
        include_graph: bool = True,
        scope: SQLScope | None = None,
    ) -> SQLSearchResponse:
        """检索 SQL Schema 并构造最终响应。

        Args:
            query: 用户检索问题。
            top_k: 返回的最大表数量。
            include_values: 是否检索采样值。
            include_evidence: 是否检索业务证据。
            include_graph: 是否检索图谱边。
            scope: 可选的 Schema/表/字段访问范围。

        Returns:
            包含命中项、表、值、证据和图谱边的检索响应。
        """

        limit = max(1, int(top_k or self.config.retriever_service.max_tables))
        all_tables = await self.store.list_tables() if scope is not None else []

        def table_allowed(table):
            return scope is None or scope.allows_table(
                table.raw_schema_name,
                table.raw_table_name,
                table.random_table_name,
            )

        table_hits = await self.store.search_tables(query, limit=limit)
        value_hits = (
            await self.store.search_values(query, limit=limit)
            if include_values
            else []
        )
        evidence_hits = (
            await self.store.search_evidence(query, limit=limit)
            if include_evidence
            else []
        )

        embedding_config = self.config.retriever_service.index.embedding
        if embedding_config.enabled:
            vectors = await self.embedder.embed([query])
            if vectors:
                table_hits = _merge_hits(
                    table_hits,
                    await self.store.search_vector(
                        vectors[0],
                        kind="table",
                        limit=limit,
                        model_name=embedding_config.model_name,
                    ),
                    limit,
                )
                if include_values:
                    value_hits = _merge_hits(
                        value_hits,
                        await self.store.search_vector(
                            vectors[0],
                            kind="value",
                            limit=limit,
                            model_name=embedding_config.model_name,
                        ),
                        limit,
                    )
                if include_evidence:
                    evidence_hits = _merge_hits(
                        evidence_hits,
                        await self.store.search_vector(
                            vectors[0],
                            kind="evidence",
                            limit=limit,
                            model_name=embedding_config.model_name,
                        ),
                        limit,
                    )

        table_hits = [
            (table, score) for table, score in table_hits if table_allowed(table)
        ]
        value_hits = [
            (value, score)
            for value, score in value_hits
            if (
                scope is None
                or (
                    (table := next(
                        (
                            item
                            for item in all_tables
                            if item.raw_table_name == value.raw_table_name
                            or item.random_table_name == value.random_table_name
                        ),
                        None,
                    ))
                    is not None
                    and table_allowed(table)
                    and scope.allows_column(
                        value.raw_table_name,
                        value.column_name,
                        value.random_table_name,
                    )
                )
            )
        ]
        value_counts = await self.store.get_field_value_counts(
            [
                value.id
                for value, _score in value_hits
                if value.id is not None
            ]
        )
        grouped_values: dict[str, tuple[SQLFieldValueSearchResult, float]] = {}
        for value, score in value_hits:
            key = normalize_field_value(value.raw_value)
            binding = SQLFieldValueBinding(
                value_index_id=int(value.id or 0),
                database_id=value.database_id,
                schema_name=value.schema_name,
                raw_table_name=value.raw_table_name,
                random_table_name=value.random_table_name,
                column_name=value.column_name,
                raw_value=value.raw_value,
                value_count=value_counts.get(int(value.id or 0), 0),
                score=score,
            )
            current = grouped_values.get(key)
            if current is None:
                grouped_values[key] = (
                    SQLFieldValueSearchResult(
                        raw_value=value.raw_value,
                        aliases=value.aliases,
                        retrieval_text=value.retrieval_text,
                        bindings=[binding],
                        total_value_count=binding.value_count,
                    ),
                    score,
                )
                continue
            representative, representative_score = current
            aliases = list(dict.fromkeys([*representative.aliases, *value.aliases]))
            bindings = [*representative.bindings, binding]
            if score > representative_score:
                grouped_values[key] = (
                    SQLFieldValueSearchResult(
                        raw_value=value.raw_value,
                        aliases=aliases,
                        retrieval_text=value.retrieval_text,
                        bindings=bindings,
                        total_value_count=(
                            representative.total_value_count
                            + binding.value_count
                        ),
                    ),
                    score,
                )
            else:
                grouped_values[key] = (
                    representative.model_copy(
                        update={
                            "aliases": aliases,
                            "bindings": bindings,
                            "total_value_count": (
                                representative.total_value_count
                                + binding.value_count
                            ),
                        }
                    ),
                    representative_score,
                )
        value_hits = list(grouped_values.values())
        unauthorized_names = {
            name
            for table in all_tables
            if not table_allowed(table)
            for name in (table.raw_table_name, table.random_table_name)
        }
        evidence_hits = [
            (item, score)
            for item, score in evidence_hits
            if not unauthorized_names.intersection(
                f"{item.retrieval_text}\n{item.evidence_content}"
            )
        ]

        table_by_key = {
            (table.raw_schema_name, table.raw_table_name): table
            for table, _score in table_hits
        }
        for value, _score in value_hits:
            for binding in value.bindings:
                table = await self.store.get_table(binding.raw_table_name)
                if table is not None:
                    table_by_key.setdefault(
                        (table.raw_schema_name, table.raw_table_name),
                        table,
                    )
        tables = list(table_by_key.values())[:limit]
        if scope is not None:
            tables = [
                table
                for table in tables
                if table_allowed(table)
            ]
            tables = [
                table.model_copy(
                    update={
                        "columns": [
                            column
                            for column in table.columns
                            if scope.allows_column(
                                table.raw_table_name,
                                column.name,
                                table.random_table_name,
                            )
                        ]
                    }
                )
                for table in tables
            ]

        graph_edges = []
        if include_graph and self.config.retriever_service.index.graph.enabled:
            for table in tables:
                graph_edges.extend(
                    await self.store.graph_neighbors(
                        table.raw_table_name,
                        max_hops=self.config.retriever_service.max_join_hops,
                        include_candidates=self.config.retriever_service.index.graph.include_candidates,
                        limit=limit,
                    )
                )
            if scope is not None:
                graph_edges = [
                    edge
                    for edge in graph_edges
                    if scope.allows_table(
                        edge.raw_schema_name,
                        edge.raw_table_name,
                        edge.random_table_name,
                    )
                    and scope.allows_table(
                        edge.related_raw_schema_name,
                        edge.related_raw_table_name,
                        edge.related_random_table_name,
                    )
                    and scope.allows_column(
                        edge.raw_table_name,
                        edge.raw_column_name or "",
                        edge.random_table_name,
                    )
                    and scope.allows_column(
                        edge.related_raw_table_name,
                        edge.related_raw_column_name or "",
                        edge.related_random_table_name,
                    )
                ]
        graph_edges = _dedupe_edges(graph_edges)[: max(limit, limit * 2)]

        visible_table_hits = table_hits
        visible_tables = tables
        visible_values = [value for value, _score in value_hits]
        visible_evidence = [item for item, _score in evidence_hits]
        visible_graph_edges = graph_edges

        if not self.config.security.allow_real_table_names:
            all_tables = await self.store.list_tables()
            aliases = {
                table.raw_table_name: table.random_table_name
                for table in all_tables
                if table.raw_table_name != table.random_table_name
            }
            projected_tables = {}
            for table in [*visible_tables, *(table for table, _score in visible_table_hits)]:
                key = (table.raw_schema_name, table.raw_table_name)
                if key in projected_tables:
                    continue

                columns = [
                    column.model_copy(
                        update={
                            "name": column.name,
                            "random_name": column.name,
                            "comment": _mask(column.comment, aliases),
                        }
                    )
                    for column in table.columns
                    if scope is None
                    or scope.allows_column(
                        table.raw_table_name,
                        column.name,
                        table.random_table_name,
                    )
                ]
                foreign_keys = [
                    foreign_key.model_copy(
                        update={
                            "referred_table": aliases.get(
                                foreign_key.referred_table,
                                foreign_key.referred_table,
                            ),
                            "referred_column": _mask(
                                foreign_key.referred_column,
                                aliases,
                            ),
                        }
                    )
                    for foreign_key in table.foreign_keys
                ]
                projected_tables[key] = table.model_copy(
                    update={
                        "raw_table_name": table.random_table_name,
                        "raw_table_label": table.random_table_name,
                        "raw_ddl_text": table.random_ddl_text,
                        "raw_schema_summary": table.random_schema_summary,
                        "columns": columns,
                        "foreign_keys": foreign_keys,
                        "qa_text": _mask(table.qa_text, aliases),
                    }
                )

            visible_tables = [
                projected_tables[(table.raw_schema_name, table.raw_table_name)]
                for table in tables
            ]
            visible_table_hits = [
                (
                    projected_tables[(table.raw_schema_name, table.raw_table_name)],
                    score,
                )
                for table, score in table_hits
            ]
            visible_values = [
                value.model_copy(
                    update={
                        "aliases": [_mask(item, aliases) for item in value.aliases],
                        "retrieval_text": _mask(value.retrieval_text, aliases),
                        "bindings": [
                            binding.model_copy(
                                update={
                                    "raw_table_name": aliases.get(
                                        binding.raw_table_name,
                                        binding.random_table_name,
                                    ),
                                }
                            )
                            for binding in value.bindings
                        ],
                    }
                )
                for value, _score in value_hits
            ]
            visible_evidence = [
                item.model_copy(
                    update={
                        "triggers": [
                            _mask(trigger, aliases) for trigger in item.triggers
                        ],
                        "retrieval_text": _mask(item.retrieval_text, aliases),
                        "evidence_content": _mask(item.evidence_content, aliases),
                        "description": _mask(item.description, aliases),
                    }
                )
                for item, _score in evidence_hits
            ]
            visible_graph_edges = [
                edge.model_copy(
                    update={
                        "raw_table_name": aliases.get(
                            edge.raw_table_name,
                            edge.random_table_name,
                        ),
                        "related_raw_table_name": aliases.get(
                            edge.related_raw_table_name,
                            edge.related_random_table_name,
                        ),
                        "raw_column_name": edge.raw_column_name,
                        "random_column_name": edge.raw_column_name,
                        "related_raw_column_name": edge.related_raw_column_name,
                        "related_random_column_name": edge.related_raw_column_name,
                        "join_condition": _mask(edge.join_condition, aliases),
                        "evidence_content": _mask(edge.evidence_content, aliases),
                    }
                )
                for edge in graph_edges
            ]

        hits: list[SQLSearchHit] = [
            SQLSearchHit(kind="table", score=score, table=table)
            for table, score in visible_table_hits
        ]
        hits.extend(
            SQLSearchHit(kind="value", score=score, value=value)
            for (value, score), value in zip(value_hits, visible_values)
        )
        hits.extend(
            SQLSearchHit(kind="evidence", score=score, evidence=evidence)
            for (item, score), evidence in zip(evidence_hits, visible_evidence)
        )
        hits.extend(
            SQLSearchHit(kind="graph", score=edge.confidence, edge=edge)
            for edge in visible_graph_edges
        )
        hits.sort(key=lambda item: (-item.score, item.kind))

        return SQLSearchResponse(
            query=query,
            hits=hits,
            tables=visible_tables,
            values=visible_values,
            evidence=visible_evidence,
            graph_edges=visible_graph_edges,
        )


def _dedupe_edges(edges):
    """按关系端点和类型去重图谱边。"""

    seen: set[tuple] = set()
    result = []
    for edge in edges:
        key = (
            edge.raw_schema_name,
            edge.raw_table_name,
            edge.raw_column_name,
            edge.related_raw_schema_name,
            edge.related_raw_table_name,
            edge.related_raw_column_name,
            edge.relation_type,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


def _merge_hits(left, right, limit: int):
    """合并关键词和向量命中并保留每项最高分。"""

    scores = {}
    for item, score in [*left, *right]:
        key = getattr(item, "id", None)
        if key is None:
            key = repr(item.model_dump(mode="json"))
        scores[key] = (item, max(float(score), scores.get(key, (item, 0.0))[1]))
    result = list(scores.values())
    result.sort(key=lambda item: -item[1])
    return result[: max(1, int(limit))]


def _mask(value: str, aliases: dict[str, str]) -> str:
    """在文本中替换原始表名，较长名称优先。"""

    text = value or ""
    for raw, random in sorted(aliases.items(), key=lambda item: -len(item[0])):
        text = text.replace(raw, random)
    return text
