"""DataAgent service ability 配置合同。"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, Literal

from deerflow.agents.service_agent.config import BaseServiceAbilityConfig
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

_DATABASE_TYPE_ALIASES = {
    "mysql": "mysql",
    "mysql_pymysql": "mysql",
    "pg": "postgresql",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "postgresqls": "postgresql",
}
_SECRET_REF_PATTERN = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*|secret://[^\s]+)$")


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

    # ADD: 阻止 Pydantic 把 Python bool 静默转换成 SQL 数值预算 1/0。
    @field_validator(
        "statement_timeout_seconds",
        "max_execution_attempts",
        "max_rows",
        "max_cell_chars",
        "max_result_chars",
        mode="before",
    )
    @classmethod
    def _reject_boolean_budget(cls, value: object) -> object:
        """拒绝布尔类型的 SQL 数值预算。"""
        if isinstance(value, bool):
            raise ValueError("SQL 数值预算不能使用布尔值。")
        return value

    @field_validator("database_type", mode="before")
    @classmethod
    def _normalize_database_type(cls, value: object) -> str:
        """归一化数据库类型并拒绝未支持方言。"""
        if not isinstance(value, str):
            raise ValueError("sql_execution.database_type 必须是字符串。")
        normalized = value.strip().lower().replace("-", "_")
        if normalized not in _DATABASE_TYPE_ALIASES:
            raise ValueError("SQL 执行只支持 PostgreSQL 或 MySQL。")
        return _DATABASE_TYPE_ALIASES[normalized]

    @field_validator("dsn_env")
    @classmethod
    def _validate_dsn_reference(cls, value: str) -> str:
        """校验 DSN 只能引用环境变量或登记的 Secret。"""
        if not isinstance(value, str) or not _SECRET_REF_PATTERN.fullmatch(value.strip()):
            raise ValueError("sql_execution.dsn_env 必须是环境变量名或 secret:// 引用。")
        return value.strip()

    @field_validator("allowed_schemas")
    @classmethod
    def _validate_allowed_schemas(cls, values: list[str]) -> list[str]:
        """校验允许访问的 Schema 配置格式。"""
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("sql_execution.allowed_schemas 不能包含空值。")
        return [item.strip() for item in values]

    @model_validator(mode="after")
    def _require_readonly(self) -> SqlExecutionConfig:
        """强制 SQL 执行使用只读开关。"""
        if self.readonly is not True:
            raise ValueError("sql_execution.readonly 必须为 true。")
        return self

class DataAgentServiceAbilityConfig(BaseModel):
    """DataAgent service ability 配置参数"""

    # ADD: 为 DataAgent 固定能力类型和版本，阻止适配器猜测配置含义。
    model_config = ConfigDict(extra="allow")
    # 审批模式: auto=自动审批, on_ambiguity=仅在意图不明确时审批, always=总是审批.
    confirmation_mode: Literal["auto", "on_ambiguity", "always"] = "on_ambiguity"
    # 是否允许子代理执行 SQL
    enable_subagent_sql_execution: bool = True
    # TODO 其他参数需要增加

    type: Literal["data_query"]  # TODO 删除
    version: Literal[1]  # TODO 删除
    enable_sql_rag: Literal[True] = True  # TODO 删除
    table_rag_config: str = Field(min_length=1, max_length=500)  # TODO 删除
    data_source_id: str = Field(min_length=1, max_length=128)  # TODO 删除
    source_binding_mode: Literal["same_physical_target", "logical_data_source"] = "same_physical_target"  # TODO 删除

    min_auto_confidence: float = Field(default=0.85, ge=0.0, le=1.0)  # TODO 删除
    sql_subagent_name: str = Field(default="sql-subagent", min_length=1, max_length=100)

    sql_execution: SqlExecutionConfig

    @model_validator(mode="after")
    def _validate_sql_requirement(self) -> DataAgentServiceAbilityConfig:
        """启用 SQL-RAG 时确保 允许开启 SQL 执行"""
        if self.enable_sql_rag and not self.sql_execution.enabled:
            raise ValueError("enable_sql_rag=true 时 sql_execution.enabled 必须为 true。")
        return self

    def public_metadata(self) -> dict[str, Any]:
        """返回可以暴露给 Agents API 和运行 metadata 的脱敏摘要。"""
        return {
            "enable_subagent_sql_execution": self.enable_subagent_sql_execution,

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
