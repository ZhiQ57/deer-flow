"""VSSAG 文档索引配置。"""

from pydantic import BaseModel, Field

from .index_embedding_config import IndexEmbeddingConfig
from .index_graph_config import IndexGraphConfig
from .index_sql_config import SQLAbilityConfig


class IndexLLMConfig(BaseModel):
    """索引增强使用的 LLM 配置。"""

    model: str = Field(default="gpt-4o-mini", description="索引增强 LLM 模型名称。")


class IndexConfig(BaseModel):
    """VSSAG 文档索引配置。"""

    enabled: bool = Field(default=True, description="是否启用文档索引。")
    llm: IndexLLMConfig = Field(
        default_factory=IndexLLMConfig,
        description="索引增强 LLM 配置。",
    )
    embedding: IndexEmbeddingConfig = Field(
        default_factory=IndexEmbeddingConfig,
        description="向量索引配置。",
    )
    graph: IndexGraphConfig = Field(
        default_factory=IndexGraphConfig,
        description="图谱索引配置。",
    )
    sql_ability: SQLAbilityConfig = Field(
        default_factory=SQLAbilityConfig,
        description="SQL Schema 索引能力配置。",
    )
