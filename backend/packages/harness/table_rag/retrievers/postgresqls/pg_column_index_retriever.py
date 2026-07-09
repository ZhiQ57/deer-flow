"""列字段索引检索器模块。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...providers.embedding import EmbeddingProvider, embed_query_vector

from ..base import ColumnRetrieverBase
from ..utils import merge_column_keyword_hits, normalize_retrieval_keywords
from .postgres_common import (
    ConnectionProvider,
    execute_sql,
    format_pgvector,
    jsonb_list,
    qualified_table,
    require_sql,
    retrieval_candidate_limit,
)
from ...schemas import ColumnRetrievalResult, ColumnTableMapping, RetrievalOptions
from ...configs import ColumnRetrievalSettings, IndexStoreSettings


class PostgresColumnIndexRetriever(ColumnRetrieverBase):
    """PostgreSQL 列字段召回器，基于列索引表召回字段并反查所属表。"""

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        index_store: IndexStoreSettings,
        retrieval_settings: ColumnRetrievalSettings,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        """初始化列字段召回器。

        Args:
            connection_provider: 外部注入的 Schema 索引库连接提供器。
            index_store: 索引存储配置。
            retrieval_settings: 列召回权重配置。
            embedding_provider: 可选查询向量生成器；为空时只做关键词和模糊召回。

        Returns:
            None。
        """
        self.connection_provider = connection_provider
        self.index_store = index_store
        self.retrieval_settings = retrieval_settings
        self.embedding_provider = embedding_provider

    def search_columns(self, query: str, options: RetrievalOptions) -> list[ColumnRetrievalResult]:
        """召回与用户问题相关的候选字段。

        Args:
            query: 用户问题或检索片段。
            options: 检索数量和最低分配置。

        Returns:
            字段召回结果列表。
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        vector_text = self._query_vector_text(clean_query)
        statement, params = self._build_search_sql(clean_query, vector_text, options)
        with self.connection_provider.connect() as conn:
            with conn.cursor() as cur:
                execute_sql(cur, statement, params)
                rows = cur.fetchall()

        return [
            ColumnRetrievalResult(
                table_name=row[0],
                column_name=row[1],
                column_comment=row[2],
                column_entities=jsonb_list(row[3]),
                score=float(row[7] or 0.0),
                source_scores={
                    "bm25": float(row[4] or 0.0),
                    "fuzzy": float(row[5] or 0.0),
                    "vector": float(row[6] or 0.0),
                },
                metadata={"retrieval_text": row[8]},
            )
            for row in rows
        ]

    def search_columns_keylist(
        self,
        keywords: Sequence[str],
        options: RetrievalOptions,
    ) -> list[ColumnRetrievalResult]:
        """按关键词列表逐个召回候选字段并融合去重。

        Args:
            keywords: 已抽取好的关键词列表。
            options: 检索数量和最低分配置。

        Returns:
            融合后的字段召回结果列表。
        """
        clean_keywords = normalize_retrieval_keywords(keywords)
        if not clean_keywords:
            return []
        return merge_column_keyword_hits(
            [(keyword, self.search_columns(keyword, options)) for keyword in clean_keywords]
        )[: options.column_top_k]

    def tables_for_columns(self, column_names: Sequence[str]) -> list[ColumnTableMapping]:
        """根据字段名反查所属表。

        Args:
            column_names: 字段名列表。

        Returns:
            字段到表的反向映射列表。
        """
        names = [name for name in dict.fromkeys(column_names) if name]
        if not names:
            return []

        sql = require_sql()
        statement = sql.SQL(
            """
            SELECT column_name, table_name, column_comment
            FROM {table}
            WHERE column_name = ANY(%(column_names)s::text[])
            ORDER BY column_name, table_name
            """
        ).format(table=self._table())
        with self.connection_provider.connect() as conn:
            with conn.cursor() as cur:
                execute_sql(cur, statement, {"column_names": names})
                rows = cur.fetchall()
        return [ColumnTableMapping(column_name=row[0], table_name=row[1], column_comment=row[2]) for row in rows]

    def _build_search_sql(
        self,
        query: str,
        vector_text: str | None,
        options: RetrievalOptions,
    ) -> tuple[Any, dict[str, Any]]:
        """构造列字段召回 SQL。

        Args:
            query: 原始查询文本。
            vector_text: pgvector 查询向量文本；为空时禁用向量得分。
            options: 检索参数。

        Returns:
            SQL 语句和参数字典。
        """
        sql = require_sql()
        vector_expr = sql.SQL("0.0::double precision")
        vector_predicate = sql.SQL("FALSE")
        params: dict[str, Any] = {
            "query": query,
            "limit": options.column_top_k,
            "candidate_limit": retrieval_candidate_limit(options.column_top_k),
            "min_score": options.column_min_score,
            "bm25_weight": self.retrieval_settings.bm25_weight,
            "fuzzy_weight": self.retrieval_settings.fuzzy_weight,
            "vector_weight": self.retrieval_settings.vector_weight,
        }
        if vector_text:
            # pgvector 的 <=> 是 cosine distance，1 - distance 作为向量相似度。
            vector_expr = sql.SQL("GREATEST(0.0, 1.0 - (embedding <=> %(query_vector)s::vector))")
            vector_predicate = sql.SQL("embedding IS NOT NULL")
            params["query_vector"] = vector_text

        trigram_clause = sql.SQL("FALSE")
        trigram_score = sql.SQL("0.0::double precision")
        if self.index_store.enable_pg_trgm:
            # psycopg 参数格式要求把 trigram 的 % 操作符写成 %%。
            trigram_clause = sql.SQL("retrieval_text %% %(query)s")
            trigram_score = sql.SQL("similarity(retrieval_text, %(query)s)")

        # entities 字段用于中文短词精确包含召回。
        entity_match = sql.SQL(
            """
            EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(column_entities) AS entity_value
                WHERE %(query)s ILIKE '%%' || entity_value || '%%'
                   OR entity_value ILIKE '%%' || %(query)s || '%%'
            )
            """
        )
        entity_score = sql.SQL("CASE WHEN {entity_match} THEN 1.0 ELSE 0.0 END").format(
            entity_match=entity_match
        )
        # 中文字段注释和业务别名使用显式包含匹配。
        keyword_match = sql.SQL(
            """
            (
                (coalesce(column_name, '') <> '' AND %(query)s ILIKE '%%' || column_name || '%%')
                OR (coalesce(column_comment, '') <> '' AND %(query)s ILIKE '%%' || column_comment || '%%')
                OR (coalesce(column_comment, '') <> '' AND column_comment ILIKE '%%' || %(query)s || '%%')
                OR (coalesce(retrieval_text, '') <> '' AND retrieval_text ILIKE '%%' || %(query)s || '%%')
                OR (coalesce(column_name, '') <> '' AND %(query)s ILIKE '%%' || replace(column_name, '_', '') || '%%')
            )
            """
        )
        keyword_score = sql.SQL("CASE WHEN {keyword_match} THEN 1.0 ELSE 0.0 END").format(
            keyword_match=keyword_match
        )
        bm25_match = sql.SQL("FALSE")
        bm25_expr = sql.SQL("0.0::double precision")
        if self.index_store.enable_bm25:
            bm25_match = sql.SQL("retrieval_text ||| %(query)s")
            bm25_expr = sql.SQL("pdb.score(id)")
        fuzzy_expr = sql.SQL(
            "{trigram_score} + {entity_score} + {keyword_score}"
        ).format(
            trigram_score=trigram_score,
            entity_score=entity_score,
            keyword_score=keyword_score,
        )
        aggregate_score_expr = sql.SQL(
            "(MAX(bm25_score) * %(bm25_weight)s + MAX(fuzzy_score) * %(fuzzy_weight)s + "
            "MAX(vector_score) * %(vector_weight)s)"
        )
        statement = sql.SQL(
            """
            WITH bm25_hits AS (
                SELECT
                    id,
                    table_name,
                    column_name,
                    column_comment,
                    column_entities,
                    {bm25_expr} AS bm25_score,
                    0.0::double precision AS fuzzy_score,
                    0.0::double precision AS vector_score,
                    retrieval_text
                FROM {table}
                WHERE {bm25_match}
                ORDER BY bm25_score DESC
                LIMIT %(candidate_limit)s::integer
            ),
            fuzzy_hits AS (
                SELECT
                    id,
                    table_name,
                    column_name,
                    column_comment,
                    column_entities,
                    0.0::double precision AS bm25_score,
                    {fuzzy_expr} AS fuzzy_score,
                    0.0::double precision AS vector_score,
                    retrieval_text
                FROM {table}
                WHERE ({trigram_clause} OR {entity_match} OR {keyword_match})
                ORDER BY fuzzy_score DESC
                LIMIT %(candidate_limit)s::integer
            ),
            vector_hits AS (
                SELECT
                    id,
                    table_name,
                    column_name,
                    column_comment,
                    column_entities,
                    0.0::double precision AS bm25_score,
                    0.0::double precision AS fuzzy_score,
                    {vector_expr} AS vector_score,
                    retrieval_text
                FROM {table}
                WHERE {vector_predicate}
                ORDER BY vector_score DESC
                LIMIT %(candidate_limit)s::integer
            ),
            candidates AS (
                SELECT * FROM bm25_hits
                UNION ALL
                SELECT * FROM fuzzy_hits
                UNION ALL
                SELECT * FROM vector_hits
            ),
            scored AS (
                SELECT
                    table_name,
                    column_name,
                    column_comment,
                    column_entities,
                    MAX(bm25_score) AS bm25_score,
                    MAX(fuzzy_score) AS fuzzy_score,
                    MAX(vector_score) AS vector_score,
                    {aggregate_score_expr} AS score,
                    retrieval_text
                FROM candidates
                GROUP BY id, table_name, column_name, column_comment, column_entities, retrieval_text
            )
            SELECT
                table_name,
                column_name,
                column_comment,
                column_entities,
                bm25_score,
                fuzzy_score,
                vector_score,
                score,
                retrieval_text
            FROM scored
            WHERE (%(min_score)s::double precision IS NULL OR score >= %(min_score)s::double precision)
            ORDER BY score DESC, bm25_score DESC, fuzzy_score DESC, vector_score DESC
            LIMIT %(limit)s::integer
            """
        ).format(
            bm25_expr=bm25_expr,
            fuzzy_expr=fuzzy_expr,
            vector_expr=vector_expr,
            aggregate_score_expr=aggregate_score_expr,
            table=self._table(),
            bm25_match=bm25_match,
            trigram_clause=trigram_clause,
            entity_match=entity_match,
            keyword_match=keyword_match,
            vector_predicate=vector_predicate,
        )
        return statement, params

    def _query_vector_text(self, query: str) -> str | None:
        """生成 pgvector 查询向量文本。

        Args:
            query: 用户问题或检索片段。

        Returns:
            pgvector 字符串；未配置 embedding_provider 时返回 None。
        """
        if self.embedding_provider is None or not self.index_store.column_embedding_enabled:
            return None
        vector = embed_query_vector(
            self.embedding_provider,
            query,
            expected_dimension=self.index_store.column_embedding_dimension,
        )
        return format_pgvector(vector, expected_dimension=self.index_store.column_embedding_dimension)

    def _table(self):
        """获取列字段索引表的安全 SQL 标识符。

        Args:
            无。

        Returns:
            psycopg.sql.SQL 对象。
        """
        return qualified_table(self.index_store.schema_name, self.index_store.column_index_name)
