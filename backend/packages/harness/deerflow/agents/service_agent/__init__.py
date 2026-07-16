"""DataAgent service ability 扩展。"""

# ADD: 暴露 DataAgent service ability 配置和解析入口，避免业务模块互相导入实现细节。
from .config import DataQueryServiceAbilityConfig, parse_service_ability
from .registry import DataAgentServiceAbility, resolve_service_ability

__all__ = [
    "DataAgentServiceAbility",
    "DataQueryServiceAbilityConfig",
    "parse_service_ability",
    "resolve_service_ability",
]
