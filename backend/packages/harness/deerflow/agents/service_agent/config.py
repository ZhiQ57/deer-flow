"""DataAgent service ability 配置合同。"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DATABASE_TYPE_ALIASES = {
    "mysql": "mysql",
    "mysql_pymysql": "mysql",
    "pg": "postgresql",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "postgresqls": "postgresql",
}
_SECRET_REF_PATTERN = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*|secret://[^\s]+)$")
logger = logging.getLogger(__name__)


class SqlExecutionConfig(BaseModel):
    """SQL 只读执行配置。"""

    # ADD: 定义 DataAgent SQL 执行边界，支持受控的 PostgreSQL/MySQL 只读闭环。
    model_config = ConfigDict(extra="allow")

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
    allowed_tables: list[str] = Field(default_factory=list)
    allowed_columns: list[str] = Field(default_factory=list)

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

    @field_validator("allowed_schemas", "allowed_tables", "allowed_columns")
    @classmethod
    def _validate_allowlist(cls, values: list[str]) -> list[str]:
        """校验数据库对象白名单格式。"""
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("SQL 对象白名单不能包含空值。")
        return [item.strip() for item in values]

    @model_validator(mode="after")
    def _require_readonly(self) -> SqlExecutionConfig:
        """强制 SQL 执行使用只读开关。"""
        if self.readonly is not True:
            raise ValueError("sql_execution.readonly 必须为 true。")
        return self


class DataQueryServiceAbilityConfig(BaseModel):
    """DataAgent service ability 配置参数"""

    # ADD: 为 DataAgent 固定能力类型和版本，阻止适配器猜测配置含义。
    model_config = ConfigDict(extra="allow")

    type: Literal["data_query"]
    version: Literal[1]
    # ADD: data_query v1 只定义完整 TableRAG→SQL 闭环；停用时应移除 service_ability，避免半装配状态。
    enable_sql_rag: Literal[True] = True
    table_rag_config: str = Field(min_length=1, max_length=500)
    data_source_id: str = Field(min_length=1, max_length=128)
    source_binding_mode: Literal["same_physical_target", "logical_data_source"] = "same_physical_target"
    confirmation_mode: Literal["auto", "on_ambiguity", "always"] = "on_ambiguity"
    min_auto_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    sql_subagent_name: str = Field(default="sql-subagent", min_length=1, max_length=100)
    sql_execution: SqlExecutionConfig

    # ADD: 自动确认阈值必须是数值，不能把 true/false 解释成 1/0。
    @field_validator("min_auto_confidence", mode="before")
    @classmethod
    def _reject_boolean_confidence(cls, value: object) -> object:
        """拒绝布尔类型的自动确认阈值。"""
        if isinstance(value, bool):
            raise ValueError("min_auto_confidence 不能使用布尔值。")
        return value

    @field_validator("table_rag_config", "data_source_id", "sql_subagent_name")
    @classmethod
    def _strip_required_strings(cls, value: str) -> str:
        """去除配置字符串首尾空白。"""
        return value.strip()

    @model_validator(mode="after")
    def _validate_sql_requirement(self) -> DataQueryServiceAbilityConfig:
        """启用 SQL RAG 时确保执行合同已经完整提供。"""
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
