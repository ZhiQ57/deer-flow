"""PostgreSQL 字段值索引检索器模块。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...configs import IndexStoreSettings, ValueIndexSearchOptions
from ...schemas import RetrievalOptions, ValueRetrievalResult
from ...utils.text import simple_text_normalize
from ..base import ValueRetrieverBase
from ..utils import merge_value_keyword_hits, normalize_retrieval_keywords
from .postgres_common import (
    ConnectionProvider,
    execute_sql,
    qualified_table,
    require_sql,
    retrieval_candidate_limit,
)


class PostgresValueIndexRetriever(ValueRetrieverBase):
    """PostgreSQL 字段值召回器，基于 BM25 和 trigram 执行混合检索。"""

    def __init__(self, connection_provider: ConnectionProvider, index_store: IndexStoreSettings):
        """初始化 PostgreSQL 字段值召回器。

        Args:
            connection_provider: 外部注入的索引库连接提供器。
            index_store: 索引表和检索参数配置。

        Returns:
            None。
        """
        self.connection_provider = connection_provider
        self.index_store = index_store

    def search_values(self, query: str, options: RetrievalOptions) -> list[ValueRetrievalResult]:
        """召回候选字段值。

        Args:
            query: 用户问题或检索片段。
            options: 检索数量和过滤参数。

        Returns:
            字段值召回结果列表。
        """
        search_options = ValueIndexSearchOptions(
            limit=options.value_top_k,
            table_names=options.table_names,
            column_names=options.column_names,
            min_score=options.value_min_score,
        )
        return self.search(query=query, options=search_options)

    def search_values_keylist(
        self,
        keywords: Sequence[str],
        options: RetrievalOptions,
    ) -> list[ValueRetrievalResult]:
        """按关键词列表逐个召回候选字段值并融合去重。

        Args:
            keywords: 已抽取好的关键词列表。
            options: 检索数量和过滤参数。

        Returns:
            融合后的字段值召回结果列表。
        """
        clean_keywords = normalize_retrieval_keywords(keywords)
        if not clean_keywords:
            return []
        return merge_value_keyword_hits(
            [(keyword, self.search_values(keyword, options)) for keyword in clean_keywords]
        )[: options.value_top_k]

    def search(self, query: str, options: ValueIndexSearchOptions | None = None) -> list[ValueRetrievalResult]:
        """检索和用户表达相似的字段值。

        Args:
            query: 用户问题片段或待对齐字段值表达。
            options: 检索过滤、数量和最低分配置。

        Returns:
            按相关性排序的字段值检索结果。
        """
        options = options or ValueIndexSearchOptions()
        normalized_query = simple_text_normalize(query)
        if not query.strip() and not normalized_query:
            return []
        return self._search_named(query=query, normalized_query=normalized_query, options=options)

    def _search_named(
        self,
        query: str,
        normalized_query: str,
        options: ValueIndexSearchOptions,
    ) -> list[ValueRetrievalResult]:
        """执行 PostgreSQL BM25 + trigram 混合检索。

        Args:
            query: 原始查询文本。
            normalized_query: 归一化后的查询文本。
            options: 检索过滤、数量和最低分配置。

        Returns:
            字段值检索结果列表。
        """
        sql = require_sql()
        filter_sql = [sql.SQL("TRUE")]
        params: dict[str, Any] = {
            "raw_query": query,
            "normalized_query": normalized_query,
            "min_score": options.min_score,
            "limit": options.limit,
            "candidate_limit": retrieval_candidate_limit(options.limit),
            "table_names": options.table_names,
            "column_names": options.column_names,
        }
        if options.table_names:
            filter_sql.append(sql.SQL("table_name = ANY(%(table_names)s::text[])"))
        if options.column_names:
            filter_sql.append(sql.SQL("column_name = ANY(%(column_names)s::text[])"))

        contains_clause = sql.SQL(
            """
            (coalesce(raw_value, '') <> '' AND position(lower(raw_value) in lower(%(raw_query)s)) > 0)
            OR (
                coalesce(retrieval_text, '') <> ''
                AND position(lower(%(normalized_query)s) in lower(retrieval_text)) > 0
            )
            OR EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(coalesce(aliases, '[]'::jsonb)) AS alias_value
                WHERE coalesce(alias_value, '') <> '' AND position(lower(alias_value) in lower(%(raw_query)s)) > 0
            )
            """
        )
        contains_score = sql.SQL("CASE WHEN {contains_clause} THEN 1.0 ELSE 0.0 END").format(
            contains_clause=contains_clause
        )

        trigram_clause = sql.SQL("FALSE")
        trigram_score = sql.SQL("0.0::double precision")
        word_similarity_score = sql.SQL("0.0::double precision")
        if self.index_store.enable_pg_trgm:
            # psycopg 使用 pyformat 占位符，trigram 的 % 操作符必须写成 %%。
            trigram_clause = sql.SQL("retrieval_text %% %(raw_query)s OR retrieval_text %% %(normalized_query)s")
            trigram_score = sql.SQL(
                """
                GREATEST(
                    similarity(retrieval_text, %(raw_query)s),
                    similarity(retrieval_text, %(normalized_query)s),
                    similarity(raw_value, %(raw_query)s),
                    coalesce((
                        SELECT max(
                            GREATEST(
                                similarity(alias_value, %(raw_query)s),
                                similarity(alias_value, %(normalized_query)s)
                            )
                        )
                        FROM jsonb_array_elements_text(coalesce(aliases, '[]'::jsonb)) AS alias_value
                    ), 0.0)
                )
                """
            )
            # word_similarity 适合长问句中包含字段值片段的场景。
            word_similarity_score = sql.SQL(
                """
                GREATEST(
                    word_similarity(retrieval_text, %(raw_query)s),
                    word_similarity(retrieval_text, %(normalized_query)s),
                    word_similarity(raw_value, %(raw_query)s),
                    coalesce((
                        SELECT max(
                            GREATEST(
                                word_similarity(alias_value, %(raw_query)s),
                                word_similarity(alias_value, %(normalized_query)s)
                            )
                        )
                        FROM jsonb_array_elements_text(coalesce(aliases, '[]'::jsonb)) AS alias_value
                    ), 0.0)
                )
                """
            )

        fuzzy_order_expr = sql.SQL(
            "({trigram_score} + {word_similarity_score} + {contains_score})"
        ).format(
            trigram_score=trigram_score,
            word_similarity_score=word_similarity_score,
            contains_score=contains_score,
        )
        bm25_clause = sql.SQL("FALSE")
        bm25_score = sql.SQL("0.0::double precision")
        if self.index_store.enable_bm25:
            bm25_clause = sql.SQL("retrieval_text ||| %(raw_query)s")
            bm25_score = sql.SQL("pdb.score(id)")
        weighted_score_expr = sql.SQL(
            """
            (
                MAX(bm25_score) * 0.53
                + MAX(trigram_score) * 0.24
                + MAX(word_similarity_score) * 0.20
                + MAX(contains_score) * 0.35
                + MAX(specificity_score) * 0.03
            )
            """
        )
        statement = sql.SQL(
            """
            WITH bm25_hits AS (
                SELECT
                    id,
                    raw_value,
                    aliases,
                    column_name,
                    table_name,
                    updated_at,
                    {bm25_score} AS bm25_score,
                    0.0::double precision AS trigram_score,
                    0.0::double precision AS word_similarity_score,
                    0.0::double precision AS contains_score,
                    LEAST(1.0, GREATEST(char_length(raw_value) - 2, 0)::double precision / 18.0) AS specificity_score
                FROM {table}
                WHERE {filters}
                  AND {bm25_clause}
                ORDER BY bm25_score DESC
                LIMIT %(candidate_limit)s::integer
            ),
            fuzzy_hits AS (
                SELECT
                    id,
                    raw_value,
                    aliases,
                    column_name,
                    table_name,
                    updated_at,
                    0.0::double precision AS bm25_score,
                    {trigram_score} AS trigram_score,
                    {word_similarity_score} AS word_similarity_score,
                    {contains_score} AS contains_score,
                    LEAST(1.0, GREATEST(char_length(raw_value) - 2, 0)::double precision / 18.0) AS specificity_score
                FROM {table}
                WHERE {filters}
                  AND ({contains_clause} OR {trigram_clause})
                ORDER BY {fuzzy_order_expr} DESC
                LIMIT %(candidate_limit)s::integer
            ),
            candidates AS (
                SELECT * FROM bm25_hits
                UNION ALL
                SELECT * FROM fuzzy_hits
            ),
            scored AS (
                SELECT
                    raw_value,
                    aliases,
                    column_name,
                    table_name,
                    updated_at,
                    MAX(bm25_score) AS bm25_score,
                    MAX(trigram_score) AS trigram_score,
                    MAX(word_similarity_score) AS word_similarity_score,
                    MAX(contains_score) AS contains_score,
                    MAX(specificity_score) AS specificity_score,
                    {weighted_score_expr} AS score
                FROM candidates
                GROUP BY id, raw_value, aliases, column_name, table_name, updated_at
            )
            SELECT *
            FROM scored
            WHERE (%(min_score)s::double precision IS NULL OR score >= %(min_score)s::double precision)
            ORDER BY score DESC, contains_score DESC, bm25_score DESC, word_similarity_score DESC, trigram_score DESC
            LIMIT %(limit)s::integer
            """
        ).format(
            bm25_score=bm25_score,
            trigram_score=trigram_score,
            word_similarity_score=word_similarity_score,
            table=self._table(),
            filters=sql.SQL(" AND ").join(filter_sql),
            bm25_clause=bm25_clause,
            contains_clause=contains_clause,
            contains_score=contains_score,
            trigram_clause=trigram_clause,
            fuzzy_order_expr=fuzzy_order_expr,
            weighted_score_expr=weighted_score_expr,
        )

        # min_score 可能为 NULL，SQL 中显式 cast 可以避免 PostgreSQL 无法推断参数类型。
        with self.connection_provider.connect() as conn:
            with conn.cursor() as cur:
                execute_sql(cur, statement, params)
                rows = cur.fetchall()

        return [
            ValueRetrievalResult(
                raw_value=row[0],
                aliases=[str(item) for item in (row[1] or [])],
                normalized_value=None,
                column_name=row[2],
                column_comment=None,
                table_name=row[3],
                table_comment=None,
                source_schema=None,
                source_table=None,
                source_column=None,
                metadata={},
                updated_at=row[4],
                score=float(row[10] or 0.0),
            )
            for row in rows
        ]

    def _table(self):
        """生成带 schema 的安全字段值索引表名。

        Args:
            无。

        Returns:
            psycopg.sql.SQL 对象，表示已正确转义的 schema.table。
        """
        return qualified_table(self.index_store.schema_name, self.index_store.field_value_table_name)
