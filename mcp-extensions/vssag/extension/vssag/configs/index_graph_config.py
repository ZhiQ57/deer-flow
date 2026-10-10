"""VSSAG 图谱索引配置。"""

from pydantic import BaseModel, Field


class IndexGraphConfig(BaseModel):
    """文档图谱索引配置。"""

    enabled: bool = Field(default=True, description="是否启用图谱索引。")
    graph_store: str = Field(
        default="./data/vssag-graph.sqlite3",
        description="图谱存储路径。",
    )
    max_join_hops: int = Field(
        default=2,
        ge=1,
        description="图谱检索最大关系跳数。",
    )
    include_candidates: bool = Field(
        default=True,
        description="是否返回候选关系。",
    )
    extraction_max_entities: int = Field(
        default=15,
        ge=1,
        description="单次实体抽取最大实体数。",
    )
    search_top_k: int = Field(
        default=20,
        ge=1,
        description="图谱索引候选数量。",
    )
    weight: float = Field(
        default=0.3,
        ge=0,
        description="图谱结果融合权重。",
    )
