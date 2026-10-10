"""VSSAG 文档解析配置。"""

from pydantic import BaseModel, Field


class MineruConfig(BaseModel):
    """MinerU 解析服务配置。"""

    mode: str = Field(default="server", description="MinerU 运行模式。")
    api_url: str = Field(default="", description="MinerU API 地址。")
    server_url: str = Field(default="", description="MinerU 服务地址。")
    backend: str = Field(default="pipeline", description="MinerU 后端类型。")
    timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        description="解析任务超时时间，单位为秒。",
    )
    poll_interval_seconds: float = Field(
        default=2.0,
        ge=0,
        description="异步解析任务轮询间隔，单位为秒。",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        description="解析任务最大重试次数。",
    )


class ParseConfig(BaseModel):
    """VSSAG 文档解析配置。"""

    enabled: bool = Field(default=True, description="是否启用文档解析。")
    engine: str = Field(default="mineru", description="文档解析引擎。")
    heading_correction_enabled: bool = Field(
        default=True,
        description="是否启用标题层级修正。",
    )
    mineru: MineruConfig = Field(
        default_factory=MineruConfig,
        description="MinerU 解析服务配置。",
    )
