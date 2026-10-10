"""VSSAG 文档检索配置。"""

from pydantic import BaseModel, Field

from .retrieval_sql_config import SQLRetrieverConfig


class VFSConfig(BaseModel):
    """VSSAG 虚拟文件系统检索配置。"""

    enabled: bool = Field(default=True, description="是否启用虚拟文件系统。")
    root_prefix: str = Field(
        default="/home/vssag",
        description="未提供外部挂载根时使用的虚拟文件系统根路径前缀。",
    )
    mount_name: str = Field(
        default="vssag",
        description="提供外部挂载根时拼接的 VSSAG 目录名。",
    )
    just_bash_worker_dir: str = Field(
        default="backend/packages/extension/just-bash-worker",
        description="VSSAG just-bash Worker 目录。",
    )
    max_full_chars: int = Field(default=10000, ge=1, description="全文读取最大字符数。")
    max_grep_results: int = Field(default=100, ge=1, description="Grep 最大返回结果数。")
    max_find_depth: int = Field(default=3, ge=1, description="Find 最大目录深度。")
    max_query_seconds: float = Field(
        default=5.0,
        gt=0,
        description="VFS 查询超时时间，单位为秒。",
    )
    allowed_modes: tuple[str, ...] = Field(
        default=("dirs", "full", "range", "lines", "heading", "block"),
        description="允许的 VFS 操作模式。",
    )


class RetrieverWeightConfig(BaseModel):
    """混合检索权重配置。"""

    text_retriever: float = Field(default=0.4, ge=0, description="全文检索权重。")
    embedding_retriever: float = Field(default=0.2, ge=0, description="向量检索权重。")
    graph_retriever: float = Field(default=0.3, ge=0, description="图谱检索权重。")
    sql_retriever: float = Field(default=0.1, ge=0, description="SQL 检索权重。")


class EmbeddingRetrieverConfig(BaseModel):
    """向量检索策略配置。"""

    enabled: bool = Field(default=True, description="是否启用向量检索。")
    top_k: int = Field(default=10, ge=1, description="向量检索 Top-K。")
    max_query_seconds: float = Field(
        default=5.0,
        gt=0,
        description="向量检索超时时间，单位为秒。",
    )


class TextRetrieverConfig(BaseModel):
    """全文检索策略配置。"""

    enabled: bool = Field(default=True, description="是否启用全文检索。")
    max_query_seconds: float = Field(
        default=5.0,
        gt=0,
        description="全文检索超时时间，单位为秒。",
    )


class GraphRetrieverConfig(BaseModel):
    """图谱检索策略配置。"""

    enabled: bool = Field(default=True, description="是否启用图谱检索。")
    max_query_seconds: float = Field(
        default=5.0,
        gt=0,
        description="图谱检索超时时间，单位为秒。",
    )
    max_join_hops: int = Field(default=2, ge=1, description="最大关系跳数。")
    result_ratio_min: float = Field(default=0.15, ge=0, description="图谱结果最小占比。")
    result_ratio_max: float = Field(default=0.30, ge=0, description="图谱结果最大占比。")
    max_entities: int = Field(default=15, ge=1, description="最大返回实体数。")
    top_k: int = Field(default=20, ge=1, description="图谱检索 Top-K。")
    weight: float = Field(default=0.3, ge=0, description="图谱检索权重。")


class RetrievalConfig(BaseModel):
    """VSSAG 文档检索配置。"""

    vfs_enabled: bool = Field(default=True, description="是否构建虚拟文件目录。")
    search_type: str = Field(
        default="all",
        description="检索方式：tool-use、vfs 或 all。",
    )
    vfs: VFSConfig = Field(
        default_factory=VFSConfig,
        description="虚拟文件系统配置。",
    )
    retriever_weight: RetrieverWeightConfig = Field(
        default_factory=RetrieverWeightConfig,
        description="混合检索权重配置。",
    )
    embedding_retriever: EmbeddingRetrieverConfig = Field(
        default_factory=EmbeddingRetrieverConfig,
        description="向量检索配置。",
    )
    text_retriever: TextRetrieverConfig = Field(
        default_factory=TextRetrieverConfig,
        description="全文检索配置。",
    )
    graph_retriever: GraphRetrieverConfig = Field(
        default_factory=GraphRetrieverConfig,
        description="图谱检索配置。",
    )
    sql_retriever: SQLRetrieverConfig = Field(
        default_factory=SQLRetrieverConfig,
        description="SQL 检索配置。",
    )
