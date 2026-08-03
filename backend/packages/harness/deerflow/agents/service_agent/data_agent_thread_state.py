"""DataAgent 版本化服务状态类型与安全降级规则。"""

# ADD: DataAgent 正式查询闭环新增，将业务状态类型从通用 ThreadState 定义中独立出来。
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict


class DataQueryServicePayload(TypedDict, total=False):
    """DataAgent 活动快照 payload 的可选业务字段。"""

    labels: dict[str, Any]
    approval: dict[str, Any]
    approval_policy: dict[str, Any]
    approval_request: dict[str, Any]
    approval_result: dict[str, Any]
    approval_error_code: str
    sql_result: dict[str, Any]
    review_items: list[dict[str, Any]]
    revision_query: str
    previous_snapshot_id: str


class ServiceState(TypedDict, total=False):
    """ThreadState 中通用、版本化且有界的服务状态快照。"""

    service_name: str
    version: int
    turn_id: str
    snapshot_id: str
    stage: str
    data_source_id: str
    payload: dict[str, Any]
    updated_at: str
    clear: bool
    label: str
    description: str
    color: str


# ADD: 未知 DataAgent 状态版本只保留身份字段并降级为只读错误，禁止恢复旧授权或 SQL 结果。
def normalize_service_state_version(item: Mapping[str, Any]) -> ServiceState:
    """规范化服务状态版本。

    Args:
        item: checkpoint 或当前 graph step 提供的服务状态。

    Returns:
        当前版本原样副本；未知 DataAgent 版本返回只读降级快照。
    """
    normalized: ServiceState = dict(item)
    if normalized.get("service_name") != "data_query" or normalized.get("version") == 1:
        return normalized
    return {
        "service_name": "data_query",
        "version": normalized.get("version", 0),
        **({"turn_id": normalized["turn_id"]} if isinstance(normalized.get("turn_id"), str) else {}),
        **({"snapshot_id": normalized["snapshot_id"]} if isinstance(normalized.get("snapshot_id"), str) else {}),
        "stage": "unsupported_version",
        "payload": {
            "error_code": "DATA_QUERY_STATE_VERSION_UNSUPPORTED",
            "read_only": True,
        },
    }
