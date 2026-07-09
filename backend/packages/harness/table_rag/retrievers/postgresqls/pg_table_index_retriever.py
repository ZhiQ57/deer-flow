"""表结构索引检索器模块。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...providers.embedding import EmbeddingProvider, embed_query_vector

from ..base import TableRetrieverBase
from ..utils import merge_table_keyword_hits, normalize_retrieval_keywords
from .postgres_common import (
    ConnectionProvider,
    execute_sql,
    format_pgvector,
    qualified_table,
    require_sql,
    retrieval_candidate_limit,
)
from ...schemas import RetrievalOptions, TableRetrievalResult
from ...configs import IndexStoreSettings, TableRetrievalSettings


class PostgresTableIndexRetriever(TableRetrieverBase):
    """PostgreSQL 表结构召回器，基于表索引表执行关键词、模糊和向量召回。"""

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        index_store: IndexStoreSettings,
        retrieval_settings: TableRetrievalSettings,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        """初始化表结构召回器。

        Args:
            connection_provider: 外部注入的 Schema 索引库连接提供器。
            index_store: 索引存储配置。
            retrieval_settings: 表召回权重配置。
            embedding_provider: 可选查询向量生成器；为空时只做关键词和模糊召回。

        Returns:
            None。
        """
        self.connection_provider = connection_provider
        self.index_store = index_store
        self.retrieval_settings = retrieval_settings
        self.embedding_provider = embedding_provider

    def search_tables(self, query: str, options: RetrievalOptions) -> list[TableRetrievalResult]:
        """召回与用户问题相关的候选表。

        Args:
            query: 用户问题或检索片段。
            options: 检索数量和最低分配置。

        Returns:
            表结构召回结果列表。
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
            TableRetrievalResult(
                table_name=row[0],
                table_label=row[1],
                table_entities=[],
                table_describe=row[2],
                score=float(row[6] or 0.0),
                source_scores={
                    "bm25": float(row[3] or 0.0),
                    "fuzzy": float(row[4] or 0.0),
                    "vector": float(row[5] or 0.0),
                },
                metadata={
                    "db_type": row[7],
                    "ddl_hash": row[8],
                },
            )
            for row in rows
        ]

    def search_tables_keylist(
        self,
        keywords: Sequence[str],
        options: RetrievalOptions,
    ) -> list[TableRetrievalResult]:
        """按关键词列表逐个召回候选表并融合去重。

        Args:
            keywords: 已抽取好的关键词列表。
            options: 检索数量和最低分配置。

        Returns:
            融合后的表结构召回结果列表。
        """
        clean_keywords = normalize_retrieval_keywords(keywords)
        if not clean_keywords:
            return []
        return merge_table_keyword_hits(
            [(keyword, self.search_tables(keyword, options)) for keyword in clean_keywords]
        )[: options.table_top_k]

    def _build_search_sql(
        self,
        query: str,
        vector_text: str | None,
        options: RetrievalOptions,
    ) -> tuple[Any, dict[str, Any]]:
        """构造表结构召回 SQL。

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
            "limit": options.table_top_k,
            "candidate_limit": retrieval_candidate_limit(options.table_top_k),
            "min_score": options.table_min_score,
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
            trigram_clause = sql.SQL("schema_summary %% %(query)s")
            trigram_score = sql.SQL("similarity(schema_summary, %(query)s)")
        
        # 中文业务名使用显式包含匹配，稳定命中表标签和表名。
        keyword_match = sql.SQL(
            """
            (
                (coalesce(table_name, '') <> '' AND %(query)s ILIKE '%%' || table_name || '%%')
                OR (coalesce(table_label, '') <> '' AND %(query)s ILIKE '%%' || table_label || '%%')
                OR (coalesce(table_label, '') <> '' AND table_label ILIKE '%%' || %(query)s || '%%')
                OR (coalesce(schema_summary, '') <> '' AND schema_summary ILIKE '%%' || %(query)s || '%%')
                OR (coalesce(table_name, '') <> '' AND %(query)s ILIKE '%%' || replace(table_name, '_', '') || '%%')
            )
            """
        )
        keyword_score = sql.SQL("CASE WHEN {keyword_match} THEN 1.0 ELSE 0.0 END").format(
            keyword_match=keyword_match
        )
        bm25_match = sql.SQL("FALSE")
        bm25_expr = sql.SQL("0.0::double precision")
        if self.index_store.enable_bm25:
            bm25_match = sql.SQL("schema_summary ||| %(query)s")
            bm25_expr = sql.SQL("pdb.score(id)")
        fuzzy_expr = sql.SQL(
            "{trigram_score} + {keyword_score}"
        ).format(
            trigram_score=trigram_score,
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
                    table_label,
                    schema_summary,
                    {bm25_expr} AS bm25_score,
                    0.0::double precision AS fuzzy_score,
                    0.0::double precision AS vector_score,
                    db_type,
                    ddl_hash
                FROM {table}
                WHERE {bm25_match}
                ORDER BY bm25_score DESC
                LIMIT %(candidate_limit)s::integer
            ),
            fuzzy_hits AS (
                SELECT
                    id,
                    table_name,
                    table_label,
                    schema_summary,
                    0.0::double precision AS bm25_score,
                    {fuzzy_expr} AS fuzzy_score,
                    0.0::double precision AS vector_score,
                    db_type,
                    ddl_hash
                FROM {table}
                WHERE ({trigram_clause} OR {keyword_match})
                ORDER BY fuzzy_score DESC
                LIMIT %(candidate_limit)s::integer
            ),
            vector_hits AS (
                SELECT
                    id,
                    table_name,
                    table_label,
                    schema_summary,
                    0.0::double precision AS bm25_score,
                    0.0::double precision AS fuzzy_score,
                    {vector_expr} AS vector_score,
                    db_type,
                    ddl_hash
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
                    table_label,
                    schema_summary,
                    MAX(bm25_score) AS bm25_score,
                    MAX(fuzzy_score) AS fuzzy_score,
                    MAX(vector_score) AS vector_score,
                    {aggregate_score_expr} AS score,
                    db_type,
                    ddl_hash
                FROM candidates
                GROUP BY id, table_name, table_label, schema_summary, db_type, ddl_hash
            )
            SELECT
                table_name,
                table_label,
                schema_summary,
                bm25_score,
                fuzzy_score,
                vector_score,
                score,
                db_type,
                ddl_hash
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
        if self.embedding_provider is None or not self.index_store.table_embedding_enabled:
            return None
        vector = embed_query_vector(
            self.embedding_provider,
            query,
            expected_dimension=self.index_store.table_embedding_dimension,
        )
        return format_pgvector(vector, expected_dimension=self.index_store.table_embedding_dimension)

    def _table(self):
        """获取表结构索引表的安全 SQL 标识符。

        Args:
            无。

        Returns:
            psycopg.sql.SQL 对象。
        """
        return qualified_table(self.index_store.schema_name, self.index_store.table_index_name)
