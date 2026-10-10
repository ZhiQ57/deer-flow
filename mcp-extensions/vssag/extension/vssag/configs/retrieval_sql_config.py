"""VSSAG SQL 检索配置。"""

from pydantic import BaseModel, Field


class SQLRetrieverConfig(BaseModel):
    """SQL Schema 检索策略配置。"""

    enabled: bool = Field(default=True, description="是否启用 SQL 检索。")
    max_query_seconds: float = Field(default=5.0, gt=0, description="SQL 检索超时时间，单位为秒。")
    max_tables: int = Field(default=20, ge=1, description="最大检索表数量。")
    max_join_hops: int = Field(default=2, ge=1, description="最大 JOIN 跳数。")
