"""VSSAG 主配置模型。"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .index_config import IndexConfig
from .index_sql_config import SQLAbilityConfig
from .model_config import ModelConfig
from .parse_config import ParseConfig
from .retrieval_config import RetrievalConfig


class ObservabilityConfig(BaseModel):
    """VSSAG 可观测性配置。"""

    enabled: bool = Field(default=False, description="是否启用链路追踪。")
    provider: str = Field(default="langfuse", description="追踪服务提供商。")
    public_key: str = Field(default="", description="Langfuse Public Key。")
    secret_key: str = Field(default="", description="Langfuse Secret Key。")
    host: str = Field(default="", description="Langfuse 服务地址。")


class VSSAGConfig(BaseModel):
    """VSSAG 运行时配置。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="是否启用 VSSAG。")
    model: ModelConfig = Field(
        default_factory=ModelConfig,
        description="模型配置。",
    )
    parsing: ParseConfig = Field(
        default_factory=ParseConfig,
        description="文档解析配置。",
    )
    index: IndexConfig = Field(
        default_factory=IndexConfig,
        description="文档索引配置。",
    )
    retrieval: RetrievalConfig = Field(
        default_factory=RetrievalConfig,
        description="文档检索配置。",
    )
    observability: ObservabilityConfig = Field(
        default_factory=ObservabilityConfig,
        description="可观测性配置。",
    )

    @classmethod
    def from_env(
        cls,
        overrides: Mapping[str, Any] | None = None,
    ) -> "VSSAGConfig":
        """从当前阶段 YAML 构造 VSSAG 配置。

        Args:
            overrides: 本次运行时使用的一次性嵌套配置覆盖。

        Returns:
            经过 YAML、进程环境和运行时覆盖合并后的配置。
        """
        from .loader import load_vssag_config

        return load_vssag_config(overrides=overrides)

    def with_overrides(self, overrides: Mapping[str, Any]) -> "VSSAGConfig":
        """返回应用运行时覆盖后的新配置。

        Args:
            overrides: 与配置对象结构相同的嵌套覆盖字典。

        Returns:
            经过 Pydantic 重新校验的新配置对象。

        Raises:
            TypeError: 覆盖值不是字典。
            ValueError: 覆盖内容无法通过配置校验。
        """
        if not isinstance(overrides, Mapping):
            raise TypeError("VSSAG 配置覆盖必须是字典。")

        values = self.model_dump()
        pending = [values]
        updates = [dict(overrides)]
        while updates:
            target = pending.pop()
            update = updates.pop()
            for key, value in update.items():
                if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                    target[key] = dict(target[key])
                    pending.append(target[key])
                    updates.append(dict(value))
                else:
                    target[key] = value
        return type(self).model_validate(values)

    @property
    def sql_ability(self) -> SQLAbilityConfig:
        """兼容旧调用方访问 vssag.index.sql_ability。"""
        return self.index.sql_ability

    @property
    def index_enabled(self) -> bool:
        """兼容旧调用方访问 vssag.index.enabled。"""
        return self.index.enabled

    @property
    def embedding_enabled(self) -> bool:
        """兼容旧调用方访问 vssag.index.embedding.enabled。"""
        return self.index.embedding.enabled

    @property
    def vector_backend(self) -> str:
        """兼容旧调用方访问向量存储引擎。"""
        return self.index.embedding.engine

    @property
    def lancedb_uri(self) -> str:
        """兼容旧调用方访问 LanceDB 路径。"""
        return self.index.embedding.vector_store

    @property
    def graph_store_path(self) -> str:
        """兼容旧调用方访问图谱存储路径。"""
        return self.index.graph.graph_store

    @property
    def pgvector_dsn(self) -> str:
        """兼容旧调用方访问 pgvector 连接串。"""
        return self.index.embedding.pgvector_dsn

    @property
    def embedding_provider(self) -> str:
        """兼容旧调用方访问 Embedding 供应商。"""
        return self.index.embedding.provider

    @property
    def embedding_model(self) -> str:
        """兼容旧调用方访问 Embedding 模型。"""
        return self.index.embedding.model_name

    @property
    def embedding_dimension(self) -> int:
        """兼容旧调用方访问 Embedding 维度。"""
        return self.index.embedding.dimension

    @property
    def openai_api_key(self) -> str:
        """返回当前默认模型供应商的 API Key。"""
        if self.model.api_key:
            return self.model.api_key
        provider = self.model.providers.get(self.model.default_provider)
        return provider.api_key if provider else ""

    @property
    def openai_base_url(self) -> str:
        """返回当前默认模型供应商的 API 地址。"""
        if self.model.base_url:
            return self.model.base_url
        provider = self.model.providers.get(self.model.default_provider)
        return provider.base_url if provider else ""

    @property
    def llm_model(self) -> str:
        """返回索引 LLM 模型名称。"""
        return self.index.llm.model

    @property
    def rerank_model(self) -> str:
        """返回重排序模型名称。"""
        return self.model.rerank_model

    @property
    def parsing_enabled(self) -> bool:
        """兼容旧调用方访问解析开关。"""
        return self.parsing.enabled

    @property
    def mineru_mode(self) -> str:
        """兼容旧调用方访问 MinerU 模式。"""
        return self.parsing.mineru.mode

    @property
    def mineru_api_url(self) -> str:
        """兼容旧调用方访问 MinerU API 地址。"""
        return self.parsing.mineru.api_url

    @property
    def mineru_server_url(self) -> str:
        """兼容旧调用方访问 MinerU 服务地址。"""
        return self.parsing.mineru.server_url

    @property
    def mineru_backend(self) -> str:
        """兼容旧调用方访问 MinerU 后端。"""
        return self.parsing.mineru.backend

    @property
    def mineru_timeout(self) -> float:
        """兼容旧调用方访问 MinerU 超时时间。"""
        return self.parsing.mineru.timeout_seconds

    @property
    def mineru_poll_interval(self) -> float:
        """兼容旧调用方访问 MinerU 轮询间隔。"""
        return self.parsing.mineru.poll_interval_seconds

    @property
    def mineru_max_retries(self) -> int:
        """兼容旧调用方访问 MinerU 重试次数。"""
        return self.parsing.mineru.max_retries

    @property
    def parsing_engine(self) -> str:
        """兼容旧调用方访问解析引擎。"""
        return self.parsing.engine

    @property
    def heading_correction_enabled(self) -> bool:
        """兼容旧调用方访问标题修正开关。"""
        return self.parsing.heading_correction_enabled

    @property
    def graph_index_enabled(self) -> bool:
        """兼容旧调用方访问图谱索引开关。"""
        return self.index.graph.enabled

    @property
    def graph_max_entities(self) -> int:
        """兼容旧调用方访问实体抽取上限。"""
        return self.index.graph.extraction_max_entities

    @property
    def graph_index_max_join_hops(self) -> int:
        """兼容旧调用方访问图谱索引跳数。"""
        return self.index.graph.max_join_hops

    @property
    def graph_include_candidates(self) -> bool:
        """兼容旧调用方访问候选关系开关。"""
        return self.index.graph.include_candidates

    @property
    def graph_enabled(self) -> bool:
        """兼容旧调用方访问图谱检索开关。"""
        return self.retrieval.graph_retriever.enabled

    @property
    def graph_weight(self) -> float:
        """兼容旧调用方访问图谱检索权重。"""
        return self.retrieval.retriever_weight.graph_retriever

    @property
    def text_retriever_enabled(self) -> bool:
        """兼容旧调用方访问全文检索开关。"""
        return self.retrieval.text_retriever.enabled

    @property
    def embedding_retriever_enabled(self) -> bool:
        """兼容旧调用方访问向量检索开关。"""
        return self.retrieval.embedding_retriever.enabled

    @property
    def text_retriever_weight(self) -> float:
        """兼容旧调用方访问全文检索权重。"""
        return self.retrieval.retriever_weight.text_retriever

    @property
    def embedding_retriever_weight(self) -> float:
        """兼容旧调用方访问向量检索权重。"""
        return self.retrieval.retriever_weight.embedding_retriever

    @property
    def sql_retriever_enabled(self) -> bool:
        """兼容旧调用方访问 SQL 检索开关。"""
        return self.retrieval.sql_retriever.enabled

    @property
    def sql_retriever_weight(self) -> float:
        """兼容旧调用方访问 SQL 检索权重。"""
        return self.retrieval.retriever_weight.sql_retriever

    @property
    def default_top_k(self) -> int:
        """兼容旧调用方访问默认向量检索数量。"""
        return self.retrieval.embedding_retriever.top_k

    @property
    def graph_top_k(self) -> int:
        """兼容旧调用方访问图谱检索数量。"""
        return self.retrieval.graph_retriever.top_k

    @property
    def graph_result_ratio_min(self) -> float:
        """兼容旧调用方访问图谱结果最小占比。"""
        return self.retrieval.graph_retriever.result_ratio_min

    @property
    def graph_result_ratio_max(self) -> float:
        """兼容旧调用方访问图谱结果最大占比。"""
        return self.retrieval.graph_retriever.result_ratio_max

    @property
    def vfs_enabled(self) -> bool:
        """兼容旧调用方访问 VFS 开关。"""
        return self.retrieval.vfs_enabled and self.retrieval.vfs.enabled

    @property
    def search_type(self) -> str:
        """兼容旧调用方访问检索方式。"""
        return self.retrieval.search_type

    @property
    def max_query_seconds(self) -> float:
        """兼容旧调用方访问向量查询超时时间。"""
        return self.retrieval.embedding_retriever.max_query_seconds

    @property
    def max_full_chars(self) -> int:
        """兼容旧调用方访问 VFS 全文长度限制。"""
        return self.retrieval.vfs.max_full_chars

    @property
    def max_grep_results(self) -> int:
        """兼容旧调用方访问 VFS Grep 限制。"""
        return self.retrieval.vfs.max_grep_results

    @property
    def max_find_depth(self) -> int:
        """兼容旧调用方访问 VFS Find 深度限制。"""
        return self.retrieval.vfs.max_find_depth

    @property
    def vfs_root_prefix(self) -> str:
        """兼容旧调用方访问 VFS 根路径前缀。"""
        return self.retrieval.vfs.root_prefix

    @property
    def vfs_mount_name(self) -> str:
        """兼容旧调用方访问 VFS 外部挂载目录名。"""
        return self.retrieval.vfs.mount_name

    @property
    def vfs_allowed_modes(self) -> tuple[str, ...]:
        """兼容旧调用方访问 VFS 操作模式。"""
        return self.retrieval.vfs.allowed_modes

    @property
    def langfuse_enabled(self) -> bool:
        """兼容旧调用方访问 Langfuse 开关。"""
        return self.observability.enabled

    @property
    def traces_provider(self) -> str:
        """兼容旧调用方访问追踪服务提供商。"""
        return self.observability.provider

    @property
    def langfuse_public_key(self) -> str:
        """兼容旧调用方访问 Langfuse Public Key。"""
        return self.observability.public_key

    @property
    def langfuse_secret_key(self) -> str:
        """兼容旧调用方访问 Langfuse Secret Key。"""
        return self.observability.secret_key

    @property
    def langfuse_host(self) -> str:
        """兼容旧调用方访问 Langfuse 服务地址。"""
        return self.observability.host
