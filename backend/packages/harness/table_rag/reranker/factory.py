"""重排序流水线装配入口。"""

from __future__ import annotations

from ..configs import RetrievalSettings
from ..providers.rerank import RerankProviderLike, RerankProviderProtocolError
from .base import RetrievalRerankerBase
from .fine_rankers import ProviderSchemaFineRanker
from .fusion import ReciprocalRankFusion, RetrievalFusionBase, WeightedSchemaFusion
from .pipeline import RankingPipeline


def build_ranking_pipeline(
    retrieval: RetrievalSettings | None = None,
    rerank_provider: RerankProviderLike | None = None,
) -> RetrievalRerankerBase:
    """根据检索配置和外部 provider 装配排序流水线。

    Args:
        retrieval: 在线检索配置。
        rerank_provider: 用户项目外部实现并注入的同步重排序 provider。

    Returns:
        可交给混合检索 Pipeline 使用的排序器。
    """
    retrieval_settings = retrieval or RetrievalSettings()
    if not retrieval_settings.reranker.enabled:
        return RankingPipeline()
    fusion = build_fusion(retrieval_settings)
    if rerank_provider is not None:
        _require_sync_rerank_provider(rerank_provider)
        return RankingPipeline(fusion=fusion, fine_ranker=ProviderSchemaFineRanker(rerank_provider))
    return RankingPipeline(fusion=fusion)


def build_fusion(retrieval: RetrievalSettings | None = None) -> RetrievalFusionBase:
    """根据检索配置装配粗排融合器。

    Args:
        retrieval: 在线检索配置。

    Returns:
        粗排融合器实例。
    """
    retrieval_settings = retrieval or RetrievalSettings()
    fusion_type = retrieval_settings.reranker.type
    if fusion_type == "weighted_schema":
        return WeightedSchemaFusion(retrieval_settings)
    if fusion_type == "rrf":
        return ReciprocalRankFusion(k=retrieval_settings.reranker.rrf_k)
    raise ValueError(f"Unsupported reranker fusion type: {fusion_type!r}")


def _require_sync_rerank_provider(rerank_provider: RerankProviderLike) -> None:
    """校验同步混合检索链路注入的是同步重排序 provider。"""
    if not (
        callable(getattr(rerank_provider, "score", None))
        or callable(getattr(rerank_provider, "score_batch", None))
    ):
        raise RerankProviderProtocolError(
            "hybrid retriever requires sync rerank provider with callable score or score_batch"
        )


__all__ = ["build_fusion", "build_ranking_pipeline"]
