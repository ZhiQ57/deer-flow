"""DataAgent service ability 配置合同。"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class SqlExecutionConfig(BaseModel):
    """SQL 只读执行配置。"""

    # ADD: 定义 DataAgent SQL 执行参数；表/字段权限由检索证据或 Executor 授权层负责。
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    database_type: str = "postgresql"
    dsn_env: str
    readonly: Literal[True] = True
    statement_timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_execution_attempts: int = Field(default=3, ge=1, le=5)
    max_rows: int = Field(default=500, ge=1, le=10_000)
    max_cell_chars: int = Field(default=2_000, ge=100, le=100_000)
    max_result_chars: int = Field(default=100_000, ge=1_000, le=1_000_000)
    allowed_schemas: list[str] = Field(default_factory=list)

    # TODO: 归一化数据库类型, 避免出现 "postgresql" vs "postgres" vs "pg" 等别名.(当前不需要实现)


class DataQueryServiceAbilityConfig(BaseModel):
    """DataAgent service ability 配置参数"""

    # ADD: 为 DataAgent 固定能力类型和版本，阻止适配器猜测配置含义。
    model_config = ConfigDict(extra="allow")
    # 审批模式: auto=自动审批, on_ambiguity=仅在意图不明确时审批, always=总是审批.
    confirmation_mode: Literal["auto", "on_ambiguity", "always"] = "on_ambiguity"
    # 是否允许子代理执行 SQL
    enable_subagent_sql_execution: bool = True
    # TODO 其他参数需要增加

    type: Literal["data_query"]         # TODO 删除
    version: Literal[1]                 # TODO 删除
    enable_sql_rag: Literal[True] = True        # TODO 删除
    table_rag_config: str = Field(min_length=1, max_length=500)     # TODO 删除
    data_source_id: str = Field(min_length=1, max_length=128)       # TODO 删除
    source_binding_mode: Literal["same_physical_target", "logical_data_source"] = "same_physical_target"  # TODO 删除
    
    min_auto_confidence: float = Field(default=0.85, ge=0.0, le=1.0)        # TODO 删除
    sql_subagent_name: str = Field(default="sql-subagent", min_length=1, max_length=100)
    sql_execution: SqlExecutionConfig

    @field_validator("table_rag_config", "data_source_id", "sql_subagent_name")
    @classmethod
    def _strip_required_strings(cls, value: str) -> str:
        """去除配置字符串首尾空白。"""
        return value.strip()

    @model_validator(mode="after")
    def _validate_sql_requirement(self) -> DataQueryServiceAbilityConfig:
        """启用 SQL-RAG 时确保 允许开启 SQL 执行"""
        if self.enable_sql_rag and not self.sql_execution.enabled:
            raise ValueError("enable_sql_rag=true 时 sql_execution.enabled 必须为 true。")
        return self

    def public_metadata(self) -> dict[str, Any]:
        """返回可以暴露给 Agents API 和运行 metadata 的脱敏摘要。"""
        return {
            "type": self.type,
            "version": self.version,
            "enabled": True,
            "enable_sql_rag": self.enable_sql_rag,
            "data_source_id": self.data_source_id,
            "source_binding_mode": self.source_binding_mode,
            "confirmation_mode": self.confirmation_mode,
            "min_auto_confidence": self.min_auto_confidence,
            "sql_subagent_name": self.sql_subagent_name,
            "database_type": self.sql_execution.database_type,
            "sql_execution_enabled": self.sql_execution.enabled,
        }


def parse_service_ability(raw: Mapping[str, Any] | None) -> DataQueryServiceAbilityConfig | None:
    """解析 DataAgent service ability 配置。

    Args:
        raw: AgentConfig 中的 service_ability 原始字典。

    Returns:
        解析后的 DataQueryServiceAbilityConfig；未配置时返回 None。

    Raises:
        TypeError: 配置不是映射对象。
        pydantic.ValidationError: 配置合同不合法。
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError("service_ability 必须是对象。")

    # ADD: 解析 DataAgent service ability 参数
    parsed = DataQueryServiceAbilityConfig.model_validate(dict(raw))
    extra_fields = sorted((parsed.model_extra or {}).keys())
    sql_extra_fields = sorted((parsed.sql_execution.model_extra or {}).keys())
    if extra_fields or sql_extra_fields:
        # ADD: 出现未知扩展字段, 打印提示
        logger.debug("DataAgent service_ability 包含未识别扩展字段：top=%s sql_execution=%s", extra_fields, sql_extra_fields)
    return parsed
