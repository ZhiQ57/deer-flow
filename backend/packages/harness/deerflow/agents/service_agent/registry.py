"""DataAgent service ability 注册表。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

from .config import DataQueryServiceAbilityConfig

logger = logging.getLogger(__name__)


class ServiceAbilityAdapter(Protocol):
    """业务能力适配器协议。"""

    name: str

    def build_tools(self) -> list[BaseTool]: ...

    def build_middlewares(self) -> list[AgentMiddleware]: ...

    def public_metadata(self) -> dict[str, Any]: ...


class DataAgentServiceAbility:
    """DataAgent 查询闭环的正式 service ability 实现。"""

    # ADD: 提供 DataAgent 专属工具和 middleware，避免污染默认 lead-agent。
    name = "data_query"

    def __init__(self, config: DataQueryServiceAbilityConfig) -> None:
        """初始化 DataAgent 能力适配器。

        Args:
            config: 已经通过 Pydantic 合同校验的能力配置。
        """
        self.config = config

    def build_tools(self) -> list[BaseTool]:
        """返回 DataAgent 当前阶段允许的业务工具。"""
        from deerflow.tools.builtins.query_labels_tool import publish_query_labels_tool

        return [publish_query_labels_tool]

    def build_middlewares(self) -> list[AgentMiddleware]:
        """返回 DataAgent 当前阶段的业务 middleware。"""
        from deerflow.agents.middlewares.query_labels_middleware import QueryLabelsMiddleware

        from .approval_middleware import QueryApprovalMiddleware
        from .sql_stage_middleware import SqlStageMiddleware
        from .table_rag_middleware import TableRagStageMiddleware
        # from .turn_reset_middleware import DataAgentTurnResetMiddleware

        # ADD: 业务 middleware 只挂在 DataAgent 适配器，默认 lead-agent 不受影响。
        return [
            # DataAgentTurnResetMiddleware(self.config),
            TableRagStageMiddleware(self.config),
            QueryLabelsMiddleware(require_retrieval=True, service_ability=self.config),
            QueryApprovalMiddleware(self.config),
            SqlStageMiddleware(self.config),
        ]

    def public_metadata(self) -> dict[str, Any]:
        """返回能力的脱敏前端/运行 metadata。"""
        return self.config.public_metadata()

    
# ADD: 安全解析 custom agent 定制智能体的 service_ability 参数 
def resolve_service_ability_safely(raw: Mapping[str, Any] | None) -> ServiceAbilityAdapter | None:
    """尝试解析 service ability 参数, 配置错误时记录脱敏修复信息并返回空适配器。
    Args:
        raw: AgentConfig.service_ability 原始配置
    Returns:
        DataAgentServiceAbility
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError("service_ability 必须是对象。")
    
    # ADD: 解析 DataAgent service ability 参数
    try:
        config = DataQueryServiceAbilityConfig.model_validate(dict(raw))
        extra_fields = sorted((config.model_extra or {}).keys())
        sql_extra_fields = sorted((config.sql_execution.model_extra or {}).keys())
        if extra_fields or sql_extra_fields:
            # ADD: 出现未知扩展字段, 打印提示
            logger.debug("DataAgent service_ability 包含未识别扩展字段：top=%s sql_execution=%s", extra_fields, sql_extra_fields)

        if config is None:
            return None
        # TODO: 该位置只支持 data-agent, 未来扩展到所有定制化智能体
        if config.type == "data_query" and config.version == 1:
            # ADD: 已经加入 middleware 了
            return DataAgentServiceAbility(config)
        raise ValueError(f"不支持的 service_ability 合同：{config.type}/v{config.version}")
    except (TypeError, ValueError) as exc:
        # ADD: 只输出字段路径和约束类型，不输出 Pydantic input 或原始配置值，避免误填 DSN 泄密。
        issues: list[str] = []
        errors = getattr(exc, "errors", None)
        if callable(errors):
            for error in errors(include_url=False, include_input=False):
                location = ".".join(str(part) for part in error.get("loc", ())) or "service_ability"
                issues.append(f"{location}: 值不符合 {error.get('type', '配置')} 约束")
        if not issues:
            issues.append("service_ability: 配置类型或版本不受支持")
        logger.error(
            "DataAgent service_ability 配置无效（%s）；修复建议：按 data_query v1 合同检查上述字段，Secret 仅填写环境变量名。",
            "; ".join(issues),
        )
        return None
