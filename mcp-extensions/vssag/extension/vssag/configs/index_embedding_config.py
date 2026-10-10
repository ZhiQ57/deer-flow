"""VSSAG 向量索引配置。"""

from pydantic import BaseModel, Field


class IndexEmbeddingConfig(BaseModel):
    """文档向量索引配置。"""

    enabled: bool = Field(
        default=False,
        description="是否启用向量索引；启用后必须配置真实 Embedding 服务。",
    )
    engine: str = Field(
        default="memory",
        description="向量存储引擎：lancedb、pgvector 或 memory。",
    )
    vector_store: str = Field(
        default="./data/vssag.lancedb",
        description="LanceDB 向量存储路径。",
    )
    pgvector_dsn: str = Field(
        default="",
        description="pgvector 数据库连接串。",
    )
    provider: str = Field(
        default="",
        description="Embedding 供应商，留空时使用默认模型供应商。",
    )
    model_name: str = Field(
        default="text-embedding-3-small",
        description="Embedding 模型名称。",
    )
    dimension: int = Field(
        default=384,
        ge=1,
        description="Embedding 向量维度。",
    )
