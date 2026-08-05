"""DataAgent 检索、标签快照和确认状态合同。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, TypedDict

from deerflow.agents.service_agent.data_agent.const import _NEW_SNAPSHOT_SOURCES, _NEW_TURN_STAGES, _RESUMABLE_STAGES, _STAGE_RANK, _TERMINAL_STAGES, DATA_QUERY_VERSION
from deerflow.agents.service_agent.contracts import ServiceState


class DataAgentServiceState(ServiceState, total=False):
    """DataAgent 业务能力字段"""

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


# ADD: 创建业务活动状态字段
def make_service_state(
    
):
    
    pass



def merge_data_query_state(
    current: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any] | None:
    """
    合并 DataAgent 状态。

    返回：
    - incoming：接受新状态；
    - 降级状态：不支持的版本；
    - None：拒绝本次更新。
    """
    state = dict(incoming)

    # 未知版本只保留身份字段，禁止恢复历史业务数据。
    if state.get("version") != DATA_QUERY_VERSION:
        fallback = {
            "service_name": "data_query",
            "version": state.get("version", 0),
            "stage": "unsupported_version",
            "payload": {
                "error_code": "DATA_QUERY_STATE_VERSION_UNSUPPORTED",
                "read_only": True,
            },
        }

        if "turn_id" in state:
            fallback["turn_id"] = state["turn_id"]

        if "snapshot_id" in state:
            fallback["snapshot_id"] = state["snapshot_id"]

        return fallback

    if current is None or current.get("version") != DATA_QUERY_VERSION:
        return state

    current_turn = current.get("turn_id")
    incoming_turn = state.get("turn_id")
    current_stage = current.get("stage")
    incoming_stage = state.get("stage")

    # 不同 turn：只允许新流程入口或显式恢复历史标签。
    if current_turn and incoming_turn and current_turn != incoming_turn:
        resumed_from = (
            (state.get("payload") or {})
            .get("resumed_from")
            or {}
        )

        resumed = (
            current_stage in _RESUMABLE_STAGES
            and incoming_stage == "labels_published"
            and resumed_from.get("turn_id") == current_turn
            and resumed_from.get("snapshot_id")
            == current.get("snapshot_id")
        )

        return state if resumed or incoming_stage in _NEW_TURN_STAGES else None

    current_snapshot = current.get("snapshot_id")
    incoming_snapshot = state.get("snapshot_id")

    # 同一 snapshot：状态只能向前推进。
    if current_snapshot == incoming_snapshot:
        if current_stage in _TERMINAL_STAGES:
            return state if incoming_stage == current_stage else None

        current_rank = _STAGE_RANK.get(current_stage)
        incoming_rank = _STAGE_RANK.get(incoming_stage)

        if current_rank is None or incoming_rank is None:
            return state if incoming_stage == current_stage else None

        return state if incoming_rank >= current_rank else None

    # 当前没有 snapshot 时允许初始化。
    if current_snapshot is None:
        return state

    # 不同 snapshot：按照业务允许的来源状态切换。
    allowed_sources = _NEW_SNAPSHOT_SOURCES.get(incoming_stage, set())

    return state if current_stage in allowed_sources else None




# ADD: 从当前状态读取 data_query 活动快照，所有业务 middleware 共用该入口。
def get_active_service_state(state: Mapping[str, Any] | None, *, service_name: str = "data_query") -> Mapping[str, Any] | None:
    """读取指定业务服务的当前快照。

    Args:
        state: LangGraph 当前线程状态。
        service_name: 服务能力名称。

    Returns:
        匹配的服务状态；没有活动快照时返回 None。
    """
    if not isinstance(state, Mapping):
        return None
    service_states = state.get("service_states")
    if isinstance(service_states, Sequence) and not isinstance(service_states, (str, bytes, bytearray)):
        for item in reversed(service_states):
            if isinstance(item, Mapping) and item.get("service_name") == service_name and item.get("clear") is not True:
                return item
    return None


# ADD: 生成最小、可持久化的 DataAgent 服务状态更新。
def make_service_state(
    *,
    turn_id: str,
    stage: str,
    payload: Mapping[str, Any] | None = None,
    snapshot_id: str | None = None,
    data_source_id: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """构造 service_states reducer 可接受的 v1 快照。"""
    body: dict[str, Any] = {
        "service_name": "data_query",
        "version": 1,
        "turn_id": turn_id,
        "stage": stage,
        "payload": dict(payload or {}),
    }
    if snapshot_id:
        body["snapshot_id"] = snapshot_id
    if data_source_id:
        body["data_source_id"] = data_source_id
    body["updated_at"] = updated_at or datetime.now(UTC).isoformat()
    return body
