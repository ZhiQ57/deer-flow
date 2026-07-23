"""DataAgent service ability 扩展。"""

# ADD: 暴露 DataAgent service ability 配置和解析入口，避免业务模块互相导入实现细节。
from .config import DataQueryServiceAbilityConfig
from .registry import DataAgentServiceAbility

__all__ = [
    "DataAgentServiceAbility",
    "DataQueryServiceAbilityConfig",
]
