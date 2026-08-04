from typing import Any, Callable, TypeAlias, TypedDict

from .data_agent.service_thread_state import merge_data_query_state


# TDD: 通用业务状态
class ServiceState(TypedDict, total=False):
    service_name: str
    version: str


StateMerger: TypeAlias = Callable[
    [ServiceState | None, ServiceState],
    ServiceState | None,
]

# TDD: 所有垂直业务统一在这里装配
STATE_MERGERS: dict[str, StateMerger] = {
    "data-agent": merge_data_query_state,
    # "clinical-agent": merge_clinical_agent_state,  # 临床智能体业务状态合并函数
}


def merge_service_states(
    existing: list[ServiceState] | None,
    updates: list[ServiceState] | None,
) -> list[ServiceState]:
    """
    合并 service_states。

    通用规则：
    - 每个 service_name 只保留一个活动状态；
    - clear=True 删除对应状态；
    - 有业务合并函数时交给业务处理；
    - 未配置的业务默认使用新状态覆盖旧状态；
    - 最近更新的状态放在末尾。
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

        merge = STATE_MERGERS.get(service_name)
        merged = (
            merge(active.get(service_name), incoming)
            if merge
            else incoming
        )

        if merged is None:
            continue

        # 重新插入，使最近更新的业务位于末尾。
        active.pop(service_name, None)
        active[service_name] = merged

    return list(active.values())