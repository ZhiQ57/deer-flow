"""DataAgent service ability 注册表。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging

from deerflow.agents.service_agent.contracts import ServiceAbilityAdapter, ServiceState
from deerflow.agents.service_agent.data_agent.service_ability import DataAgentServiceAbility
from deerflow.agents.service_agent.data_agent.service_config import DataAgentServiceAbilityConfig
from deerflow.agents.service_agent.data_agent.service_state import merge_data_query_state
from deerflow.agents.service_agent.contracts import StateMerger
from deerflow.config.agents_config import AgentConfig
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# 业务注册表抽象类
@dataclass(frozen=True)
class ServiceAbilitySpec:
    config_cls: type[BaseModel]
    adapter_cls: type[ServiceAbilityAdapter]
    state_merger: StateMerger | None = None  # 可选的业务状态合并函数

# 业务注册表字典
SERVICE_ABILITY_REGISTRY: dict[str, ServiceAbilitySpec] = {
    "data-agent": ServiceAbilitySpec(
        config_cls=DataAgentServiceAbilityConfig,   # 配置类
        adapter_cls=DataAgentServiceAbility,        # 适配器
        state_merger=merge_data_query_state,        # 业务状态合并函数
    ),
}

def resolve_service_ability_safely(app_config: AgentConfig | None) -> ServiceAbilityAdapter | None:
    """安全解析 custom-agent 的 service ability

    Args:
        app_config: `AgentConfig` 实例，包含 service_ability 配置。

    Returns:
        已解析 service_ability 能力
    """

    if app_config is None:
        return None

    # 读取智能体名称
    if isinstance(app_config, Mapping):
        name = app_config.get("service_name")   # 字典
    else:
        name = getattr(app_config, "service_name", None)  # 对象

    try:
        # 根据注册表获取智能体的能力配置
        service_ability = SERVICE_ABILITY_REGISTRY.get(name)
        if service_ability is None or app_config is None:
            return None

        # 解析能力配置
        # 例如: config = DataQueryServiceAbilityConfig.model_validate(app_config.service_ability)
        config = service_ability.config_cls.model_validate(app_config)

        # 实例化适配器
        return service_ability.adapter_cls(config)

    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 的 service_ability 解析失败: {exc}") from exc


def merge_service_states(
    existing: list[ServiceState] | None,
    updates: list[ServiceState] | None,
) -> list[ServiceState]:
    """
    合并 service_states。
    """

    active: dict[str, ServiceState] = {}

    for state in [*(existing or []), *(updates or [])]:
        service_name = state["service_name"].strip()

        if not service_name:
            raise ValueError("service_name 不能为空")

        if state.get("clear"):
            active.pop(service_name, None)
            continue

        incoming = {
            **state,
            "service_name": service_name,
        }
        incoming.pop("clear", None)

        spec = SERVICE_ABILITY_REGISTRY.get(service_name)

        merger = spec.state_merger if spec is not None else None

        merged = (
            merger(active.get(service_name), incoming)
            if merger is not None
            else incoming
        )

        if merged is None:
            continue

        # 重新插入，使最近更新的业务位于末尾。
        active.pop(service_name, None)
        active[service_name] = merged

    return list(active.values())


