"""VSSAG 模型配置。"""

from typing import Any

from pydantic import BaseModel, Field


class ModelProviderConfig(BaseModel):
    """单个模型供应商配置。"""

    api_key: str = Field(default="", description="模型供应商 API 密钥。")
    base_url: str = Field(default="", description="模型供应商 API 地址。")
    rerank_base_url: str = Field(
        default="",
        description="模型供应商原生 Rerank API 地址。",
    )
    model: str = Field(default="", description="默认模型名称。")
    extra_body: dict[str, Any] = Field(
        default_factory=dict,
        description="发送给 OpenAI 兼容接口的供应商扩展参数。",
    )


class EmbeddingModelConfig(BaseModel):
    """通用 Embedding 模型配置。"""

    model: str = Field(
        default="text-embedding-3-small",
        description="Embedding 模型名称。",
    )
    dimension: int = Field(
        default=384,
        ge=1,
        description="Embedding 向量维度。",
    )


class ModelConfig(BaseModel):
    """VSSAG 模型配置。"""

    default_provider: str = Field(
        default="openai",
        description="默认 LLM 供应商名称。",
    )
    api_key: str = Field(
        default="",
        description="当前默认供应商 API 密钥。",
    )
    base_url: str = Field(
        default="",
        description="当前默认供应商 API 地址。",
    )
    rerank_provider: str = Field(
        default="",
        description="Rerank 供应商名称，留空时使用默认供应商。",
    )
    providers: dict[str, ModelProviderConfig] = Field(
        default_factory=dict,
        description="按供应商名称保存的 LLM 配置。",
    )
    embedding: EmbeddingModelConfig = Field(
        default_factory=EmbeddingModelConfig,
        description="Embedding 模型配置。",
    )
    rerank_model: str = Field(
        default="",
        description="重排序模型名称。",
    )
