"""PostgreSQL Evidence 业务规则检索器模块。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...configs import EvidenceRetrievalSettings, IndexStoreSettings
from ...providers.embedding import EmbeddingProvider
from ...schemas import EvidenceRetrievalResult, RetrievalOptions
from ..base import EvidenceRetrieverBase
from ..utils import merge_evidence_keyword_hits, normalize_retrieval_keywords
from .postgres_common import (
    ConnectionProvider,
    execute_sql,
    jsonb_list,
    qualified_table,
    require_sql,
    retrieval_candidate_limit,
)


class PostgresEvidenceRetriever(EvidenceRetrieverBase):
    """PostgreSQL Evidence 召回器，基于触发词检索业务规则、术语和口径。"""

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        index_store: IndexStoreSettings,
        retrieval_settings: EvidenceRetrievalSettings,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        """初始化 Evidence 召回器。

        Args:
            connection_provider: 外部注入的索引库连接提供器。
            index_store: 索引存储配置。
            retrieval_settings: Evidence 召回权重配置。
            embedding_provider: 可选查询向量生成器；当前默认链路不启用向量召回。

        Returns:
            None。
        """
        self.connection_provider = connection_provider
        self.index_store = index_store
        self.retrieval_settings = retrieval_settings
        self.embedding_provider = embedding_provider

    def search_evidences(self, query: str, options: RetrievalOptions) -> list[EvidenceRetrievalResult]:
        """召回与用户问题相关的业务规则和证据。

        Args:
            query: 用户问题或检索片段。
            options: 检索数量和最低分配置。

        Returns:
            Evidence 召回结果列表。
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        statement, params = self._build_search_sql(clean_query, options)
        with self.connection_provider.connect() as conn:
            with conn.cursor() as cur:
                execute_sql(cur, statement, params)
                rows = cur.fetchall()

        return [
            EvidenceRetrievalResult(
                triggers=jsonb_list(row[0]),
                retrieval_text=row[1],
                evidence_content=row[2],
                evidence_type=row[3],
                description=row[4],
                status=int(row[5] or 0),
                score=float(row[8] or 0.0),
                source_scores={
                    "bm25": float(row[6] or 0.0),
                    "fuzzy": float(row[7] or 0.0),
                    "vector": 0.0,
                },
            )
            for row in rows
        ]

    def search_evidences_keylist(
        self,
        keywords: Sequence[str],
        options: RetrievalOptions,
    ) -> list[EvidenceRetrievalResult]:
        """按关键词列表逐个召回 Evidence 并融合去重。

        Args:
            keywords: 已抽取好的关键词列表。
            options: 检索数量和最低分配置。

        Returns:
            融合后的 Evidence 召回结果列表。
        """
        clean_keywords = normalize_retrieval_keywords(keywords)
        if not clean_keywords:
            return []
        return merge_evidence_keyword_hits(
            [(keyword, self.search_evidences(keyword, options)) for keyword in clean_keywords]
        )[: options.evidence_top_k]

    def _build_search_sql(self, query: str, options: RetrievalOptions) -> tuple[Any, dict[str, Any]]:
        """构造 Evidence 召回 SQL。

        Args:
            query: 原始查询文本。
            options: 检索参数。

        Returns:
            SQL 语句和参数字典。
        """
        sql = require_sql()
        params: dict[str, Any] = {
            "query": query,
            "limit": options.evidence_top_k,
            "candidate_limit": retrieval_candidate_limit(options.evidence_top_k),
            "min_score": options.evidence_min_score,
            "bm25_weight": self.retrieval_settings.bm25_weight,
            "fuzzy_weight": self.retrieval_settings.fuzzy_weight,
        }
        trigram_clause = sql.SQL("FALSE")
        trigram_score = sql.SQL("0.0::double precision")
        if self.index_store.enable_pg_trgm:
            trigram_clause = sql.SQL("retrieval_text %% %(query)s")
            trigram_score = sql.SQL("similarity(retrieval_text, %(query)s)")

        bm25_match = sql.SQL("FALSE")
        bm25_expr = sql.SQL("0.0::double precision")
        if self.index_store.enable_bm25:
            bm25_match = sql.SQL("retrieval_text ||| %(query)s")
            bm25_expr = sql.SQL("pdb.score(id)")

        aggregate_score_expr = sql.SQL("(MAX(bm25_score) * %(bm25_weight)s + MAX(fuzzy_score) * %(fuzzy_weight)s)")
        statement = sql.SQL(
            """
            WITH bm25_hits AS (
                SELECT
                    id,
                    triggers,
                    retrieval_text,
                    evidence_content,
                    evidence_type,
                    description,
                    status,
                    {bm25_expr} AS bm25_score,
                    0.0::double precision AS fuzzy_score
                FROM {table}
                WHERE status = 1
                  AND {bm25_match}
                ORDER BY bm25_score DESC
                LIMIT %(candidate_limit)s::integer
            ),
            fuzzy_hits AS (
                SELECT
                    id,
                    triggers,
                    retrieval_text,
                    evidence_content,
                    evidence_type,
                    description,
                    status,
                    0.0::double precision AS bm25_score,
                    {trigram_score} AS fuzzy_score
                FROM {table}
                WHERE status = 1
                  AND {trigram_clause}
                ORDER BY fuzzy_score DESC
                LIMIT %(candidate_limit)s::integer
            ),
            candidates AS (
                SELECT * FROM bm25_hits
                UNION ALL
                SELECT * FROM fuzzy_hits
            ),
            scored AS (
                SELECT
                    triggers,
                    retrieval_text,
                    evidence_content,
                    evidence_type,
                    description,
                    status,
                    MAX(bm25_score) AS bm25_score,
                    MAX(fuzzy_score) AS fuzzy_score,
                    {aggregate_score_expr} AS score
                FROM candidates
                GROUP BY id, triggers, retrieval_text, evidence_content, evidence_type, description, status
            )
            SELECT
                triggers,
                retrieval_text,
                evidence_content,
                evidence_type,
                description,
                status,
                bm25_score,
                fuzzy_score,
                score
            FROM scored
            WHERE (%(min_score)s::double precision IS NULL OR score >= %(min_score)s::double precision)
            ORDER BY score DESC, bm25_score DESC, fuzzy_score DESC
            LIMIT %(limit)s::integer
            """
        ).format(
            bm25_expr=bm25_expr,
            trigram_score=trigram_score,
            aggregate_score_expr=aggregate_score_expr,
            table=self._table(),
            bm25_match=bm25_match,
            trigram_clause=trigram_clause,
        )
        return statement, params

    def _table(self):
        """获取 Evidence 索引表的安全 SQL 标识符。"""
        return qualified_table(self.index_store.schema_name, self.index_store.evidence_index_name)
