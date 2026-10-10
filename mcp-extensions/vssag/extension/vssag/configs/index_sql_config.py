"""VSSAG SQL Schema 索引配置。"""

from typing import Literal

from pydantic import BaseModel, Field


class SQLSecurityConfig(BaseModel):
    """SQL Schema 脱敏配置。"""

    allow_real_table_names: bool = Field(
        default=True,
        description="是否允许对外暴露真实表名。",
    )


class SQLFieldValueLLMAnnotationConfig(BaseModel):
    """字段值 LLM 标注限制配置。"""

    enabled: bool = Field(default=False, description="是否启用 LLM 标注。")
    batch_size: int = Field(default=20, ge=1, description="单批处理数量。")
    timeout_seconds: float = Field(default=30.0, gt=0, description="单次 LLM 调用超时时间，单位为秒。")
    max_retries: int = Field(default=2, ge=0, description="最大重试次数。")
    requests_per_minute: int = Field(default=30, ge=1, description="每分钟最大请求数。")
    max_calls_per_job: int = Field(default=100, ge=1, description="单个任务最大调用次数。")
    max_input_characters_per_job: int = Field(default=100000, ge=1, description="单个任务最大输入字符数。")
    minimum_confidence: float = Field(default=0.8, ge=0, le=1, description="标注结果最低置信度。")
    failure_policy: Literal["pending", "fail"] = Field(
        default="pending",
        description="LLM 输出无法可靠关联时的处理方式。",
    )
    prompt_version: str = Field(default="field-value-annotation-v1", description="字段值标注 Prompt 版本。")


class SQLPostgresWALConfig(BaseModel):
    """PostgreSQL WAL 读取配置。"""

    slot_name: str = Field(default="vssag_field_values", description="复制槽名称。")
    output_plugin: str = Field(default="wal2json", description="WAL 输出插件名称。")
    poll_interval_seconds: float = Field(default=1.0, gt=0, description="WAL 轮询间隔，单位为秒。")


class SQLMySQLBinlogConfig(BaseModel):
    """MySQL Binlog 读取配置。"""

    server_id: int = Field(default=71002, ge=1, description="Binlog 客户端 ID。")
    poll_interval_seconds: float = Field(default=1.0, gt=0, description="Binlog 轮询间隔，单位为秒。")


class SQLKafkaStreamConfig(BaseModel):
    """字段值 CDC Kafka 传输配置。"""

    bootstrap_servers: tuple[str, ...] = Field(default=("127.0.0.1:9092",), description="Kafka Broker 地址列表。")
    topic: str = Field(default="nl2sql-field-values", description="Kafka Topic。")
    group_id: str = Field(default="nl2sql-field-value-index", description="Kafka Consumer Group。")
    client_id: str = Field(default="nl2sql-field-value-index", description="Kafka Client ID。")
    security_protocol: str = Field(default="PLAINTEXT", description="Kafka 安全协议。")
    sasl_mechanism: str = Field(default="PLAIN", description="Kafka SASL 认证机制。")
    sasl_username: str = Field(default="", description="Kafka SASL 用户名。")
    sasl_password: str = Field(default="", description="Kafka SASL 密码。")


class SQLRedisStreamConfig(BaseModel):
    """字段值 CDC Redis Stream 传输配置。"""

    url: str = Field(default="redis://127.0.0.1:6379/0", description="Redis 连接串。")
    stream_name: str = Field(default="nl2sql-field-values", description="Redis Stream 名称。")
    group_name: str = Field(default="nl2sql-field-value-index", description="Redis Consumer Group 名称。")
    consumer_name: str = Field(default="nl2sql-field-value-worker", description="Redis Consumer 名称。")
    block_milliseconds: int = Field(default=2000, ge=1, description="Redis 阻塞读取时间，单位为毫秒。")
    pending_idle_milliseconds: int = Field(default=30000, ge=1, description="Pending 消息空闲阈值，单位为毫秒。")


class SQLFieldValueCDCConfig(BaseModel):
    """字段值 CDC、重试和周期校准配置。"""

    enabled: bool = Field(default=False, description="是否启用 CDC。")
    transport: str = Field(default="redis_stream", description="CDC 传输方式：kafka 或 redis_stream。")
    batch_max_events: int = Field(default=500, ge=1, description="单批最大事件数。")
    batch_window_seconds: float = Field(default=2.0, gt=0, description="批处理时间窗口，单位为秒。")
    max_retries: int = Field(default=3, ge=0, description="最大重试次数。")
    retry_backoff_seconds: float = Field(default=1.0, ge=0, description="重试退避时间，单位为秒。")
    recalibration_interval_seconds: int = Field(default=86400, ge=1, description="周期校准间隔，单位为秒。")
    postgres_wal: SQLPostgresWALConfig = Field(default_factory=SQLPostgresWALConfig, description="PostgreSQL WAL 配置。")
    mysql_binlog: SQLMySQLBinlogConfig = Field(default_factory=SQLMySQLBinlogConfig, description="MySQL Binlog 配置。")
    kafka: SQLKafkaStreamConfig = Field(default_factory=SQLKafkaStreamConfig, description="Kafka Stream 配置。")
    redis_stream: SQLRedisStreamConfig = Field(default_factory=SQLRedisStreamConfig, description="Redis Stream 配置。")


class SQLFieldValueIndexConfig(BaseModel):
    """字段值索引采集、统计和安全配置。"""

    mode: str = Field(default="manual", description="字段值索引模式。")
    database_id: int = Field(default=0, ge=0, description="目标数据库 ID。")
    max_distinct_values: int = Field(default=10000, ge=1, description="单字段最大不同值数量。")
    statistics_sample_rows: int = Field(default=1000, ge=1, description="字段统计采样行数。")
    page_size: int = Field(default=500, ge=1, description="字段值分页大小。")
    query_timeout_seconds: float = Field(default=30.0, gt=0, description="字段值查询超时时间，单位为秒。")
    page_interval_seconds: float = Field(default=0.0, ge=0, description="字段值分页间隔，单位为秒。")
    max_value_length: int = Field(default=200, ge=1, description="字段值最大保存长度。")
    field_score_threshold: float = Field(default=75.0, description="自动索引字段分数阈值。")
    uncertain_score_min: float = Field(default=45.0, description="不确定字段分数下限。")
    hard_reject_sensitive: bool = Field(default=True, description="是否拒绝敏感字段值。")
    value_patterns: tuple[str, ...] = Field(default=(), description="允许参与索引的字段值模式。")
    llm_annotation: SQLFieldValueLLMAnnotationConfig = Field(
        default_factory=SQLFieldValueLLMAnnotationConfig,
        description="字段值 LLM 标注配置。",
    )
    cdc: SQLFieldValueCDCConfig = Field(
        default_factory=SQLFieldValueCDCConfig,
        description="字段值 CDC 配置。",
    )


class SQLTargetSourceConfig(BaseModel):
    """SQL Schema 目标数据库配置。"""

    type: str = Field(default="sqlite", description="目标数据库类型。")
    dsn: str = Field(default="", description="目标数据库连接串。")
    exclude_schemas: tuple[str, ...] = Field(default=(), description="排除的 Schema 列表。")
    exclude_tables: tuple[str, ...] = Field(default=(), description="排除的表列表。")
    value_columns: dict[str, tuple[str, ...]] = Field(default_factory=dict, description="手动字段值索引配置，键为表名。")
    sample_rows: int = Field(default=5, ge=0, description="表采样行数。")
    max_values_per_column: int = Field(default=100, ge=1, description="每列最大采样值数量。")
    field_value_index: SQLFieldValueIndexConfig = Field(
        default_factory=SQLFieldValueIndexConfig,
        description="字段值索引配置。",
    )


class SQLEmbeddingConfig(BaseModel):
    """SQL Schema 向量索引配置。"""

    enabled: bool = Field(default=False, description="是否启用 SQL 向量索引。")
    engine: str = Field(default="memory", description="SQL 向量存储引擎。")
    lancedb_uri: str = Field(default="./data/vssag-sql.lancedb", description="SQL LanceDB 存储路径。")
    provider: str = Field(default="", description="SQL Embedding 供应商。")
    base_url: str = Field(default="", description="SQL Embedding 服务地址。")
    model_name: str = Field(default="text-embedding-3-small", description="SQL Embedding 模型名称。")
    dimension: int = Field(default=384, ge=1, description="SQL 向量维度。")


class SQLGraphConfig(BaseModel):
    """SQL Schema 关系图谱索引配置。"""

    enabled: bool = Field(default=True, description="是否启用 SQL 图谱索引。")
    max_join_hops: int = Field(default=2, ge=1, description="最大 JOIN 跳数。")
    include_candidates: bool = Field(default=True, description="是否包含候选关系。")


class SQLQAConfig(BaseModel):
    """SQL Schema QA 增强索引配置。"""

    enabled: bool = Field(default=False, description="是否启用 QA 增强索引。")


class SQLIndexConfig(BaseModel):
    """SQL Schema 索引配置。"""

    embedding: SQLEmbeddingConfig = Field(default_factory=SQLEmbeddingConfig, description="SQL 向量索引配置。")
    graph: SQLGraphConfig = Field(default_factory=SQLGraphConfig, description="SQL 图谱索引配置。")
    qa: SQLQAConfig = Field(default_factory=SQLQAConfig, description="SQL QA 索引配置。")


class SQLRetrieverServiceConfig(BaseModel):
    """SQL Schema 检索服务配置。"""

    type: str = Field(default="sqlite", description="SQL 索引存储类型。")
    namespace: str = Field(default="sql_schema", description="索引命名空间。")
    dsn: str = Field(default="./data/vssag-sql.sqlite3", description="索引库连接串。")
    auto_create_tables: bool = Field(default=True, description="是否自动创建索引表。")
    auto_sync_times: int = Field(default=3600, ge=0, description="自动同步周期，单位为秒。")
    index: SQLIndexConfig = Field(default_factory=SQLIndexConfig, description="SQL 索引配置。")
    max_tables: int = Field(default=20, ge=1, description="最大检索表数量。")
    max_join_hops: int = Field(default=2, ge=1, description="最大 JOIN 跳数。")


class SQLAbilityConfig(BaseModel):
    """VSSAG SQL Schema 能力配置。"""

    enabled: bool = Field(default=False, description="是否启用 SQL Schema 能力。")
    vfs_root_prefix: str = Field(
        default="/home/vssag",
        description="未提供外部挂载根时使用的 SQL 虚拟目录根路径前缀。",
    )
    vfs_mount_name: str = Field(
        default="vssag",
        description="提供外部挂载根时拼接的 VSSAG 目录名。",
    )
    security: SQLSecurityConfig = Field(default_factory=SQLSecurityConfig, description="SQL 安全配置。")
    target_sources: SQLTargetSourceConfig = Field(default_factory=SQLTargetSourceConfig, description="目标数据库配置。")
    retriever_service: SQLRetrieverServiceConfig = Field(
        default_factory=SQLRetrieverServiceConfig,
        description="SQL 检索服务配置。",
    )
