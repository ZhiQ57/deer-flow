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


def resolve_nested_service_ability_safely(agent_config: AgentConfig | None) -> ServiceAbilityAdapter | None:
    """只从 AgentConfig.service_ability 嵌套块解析 service ability。

    这个方法专门给 custom agent 的 config.yaml 使用，不再把整份 AgentConfig
    直接喂给业务配置模型，避免 name/model/tool_groups/skills 等外层字段混进来。
    """
    if agent_config is None:
        return None

    raw_service_ability = getattr(agent_config, "service_ability", None)
    if raw_service_ability is None:
        return None

    if isinstance(raw_service_ability, BaseModel):
        raw_service_ability = raw_service_ability.model_dump(exclude_none=True)
    elif not isinstance(raw_service_ability, Mapping):
        raise ValueError("AgentConfig.service_ability 必须是字典或 Pydantic 模型")

    service_name = str(raw_service_ability.get("service_name") or "").strip()
    if not service_name:
        raise ValueError("AgentConfig.service_ability 缺少 service_name")

    spec = SERVICE_ABILITY_REGISTRY.get(service_name)
    if spec is None:
        raise ValueError(f"不支持的 service_ability: {service_name}")

    config = spec.config_cls.model_validate(raw_service_ability)
    return spec.adapter_cls(config)



def resolve_service_ability_safely(agent_config: AgentConfig | None) -> ServiceAbilityAdapter | None:
    """安全解析 custom-agent 的 service ability

    Args:
        app_config: `AgentConfig` 实例，包含 service_ability 配置。

    Returns:
        已解析 service_ability 能力
    """

    if agent_config is None:
        return None

    # 读取智能体名称
    if isinstance(agent_config, Mapping):
        name = agent_config.get("service_name")   # 字典
    else:
        name = getattr(agent_config, "service_name", None)  # 对象
        # TODO 这里分为两种情况, 一种是 系统外层读取 config.yaml , 一种是 读取 service_bility 参数.
    try:
        # 根据注册表获取智能体的能力配置
        service_ability = SERVICE_ABILITY_REGISTRY.get(name)
        if service_ability is None or agent_config is None:
            return None

        # 解析能力配置
        # 例如: config = DataQueryServiceAbilityConfig.model_validate(app_config.service_ability)
        config = service_ability.config_cls.model_validate(agent_config)

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

