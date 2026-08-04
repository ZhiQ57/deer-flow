"""DataAgent 检索、标签快照和确认状态合同。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, TypedDict

from deerflow.agents.service_agent.data_agent.const import _NEW_SNAPSHOT_SOURCES, _RESUMABLE_STAGES, _STAGE_RANK, _TERMINAL_STAGES, DATA_QUERY_VERSION
from deerflow.agents.service_agent.thread_state_registry import ServiceState


from .service_config import DataQueryServiceAbilityConfig

_LABEL_SOURCES = frozenset({"user", "database", "derived"})

class DataAgentServiceState(ServiceState, total=False):
    """DataAgent 业务能力字段"""

    confirmation_mode: str
    enable_subagent_sql_execution: str
    allowed_type: str
    execute_database: str
    dsn_env: str

    # 拓展字段
    extends: DataAgentExtends


class DataAgentExtends(TypedDict, total=False):
    """DataAgent 扩展状态字段。"""
    
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

def _canonical_json(value: object) -> str:
    """生成稳定 JSON 文本。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_id(prefix: str, value: object) -> str:
    """生成带类型前缀的稳定摘要标识。"""
    digest = sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}:sha256:{digest}"


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


def _normalize_label(item: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    """校验并规范化单个意图标签。"""
    label = item.get("label")
    value = item.get("value")
    source = item.get("source")
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"labels[{index}].label 不能为空。")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"labels[{index}].value 不能为空。")
    if source not in _LABEL_SOURCES:
        raise ValueError(f"labels[{index}].source 必须是 user、database 或 derived。")

    raw_refs = item.get("evidence_refs") or []
    if not isinstance(raw_refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in raw_refs):
        raise ValueError(f"labels[{index}].evidence_refs 必须是字符串数组。")
    evidence_refs = [ref.strip() for ref in raw_refs]

    normalized: dict[str, Any] = {
        "label": label.strip(),
        "value": value.strip(),
        "source": source,
        "evidence_refs": evidence_refs,
    }
    normalized_value = item.get("normalized")
    if normalized_value is not None:
        if not isinstance(normalized_value, str) or not normalized_value.strip():
            raise ValueError(f"labels[{index}].normalized 必须是非空字符串。")
        normalized["normalized"] = normalized_value.strip()
    return normalized


# ADD: 由服务端生成不可由模型覆盖的意图标签快照和 snapshot_id。
def build_query_label_snapshot(
    *,
    turn_id: str,
    data_source_id: str,
    ability_version: int,
    binding: Mapping[str, Any] | None = None,
    intent: str,
    labels: Sequence[Mapping[str, Any]],
    summary: str | None,
    ambiguities: Sequence[str],
    confidence: float | None = None,
    ambiguities_declared: bool = True,
) -> dict[str, Any]:
    """构造 DataAgent 查询标签快照。

    Args:
        confidence: 服务端或历史合同提供的可选置信度；当前模型工具不再接收该字段。
        ambiguities_declared: 模型是否明确提交了 ambiguities 字段；缺失字段不能被当作“无歧义”。
    """
    binding = binding if isinstance(binding, Mapping) else {}
    binding_fingerprint = binding.get("binding_fingerprint")
    if not isinstance(binding_fingerprint, str) or binding.get("data_source_id") != data_source_id:
        binding_fingerprint = "binding:unavailable"
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("intent 不能为空。")
    if not labels or len(labels) > 30:
        raise ValueError("labels 必须包含 1 到 30 项。")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1):
        raise ValueError("confidence 必须位于 0 到 1。")
    if len(ambiguities) > 20 or any(not isinstance(item, str) or not item.strip() for item in ambiguities):
        raise ValueError("ambiguities 必须是最多 20 项的非空字符串数组。")

    normalized_labels = [_normalize_label(item, index=index) for index, item in enumerate(labels)]
    normalized_summary = summary.strip() if isinstance(summary, str) and summary.strip() else None
    normalized_ambiguities = [item.strip() for item in ambiguities]
    context_digest = _sha256_id(
        "query-context",
        {
            "turn_id": turn_id,
            "data_source_id": data_source_id,
            "ability_version": ability_version,
            "binding_fingerprint": binding_fingerprint,
            "intent": intent.strip(),
            "summary": normalized_summary,
            "labels": normalized_labels,
            "ambiguities": normalized_ambiguities,
            "ambiguities_declared": bool(ambiguities_declared),
        },
    )
    snapshot_body = {
        "turn_id": turn_id,
        "data_source_id": data_source_id,
        "ability_version": ability_version,
        "context_digest": context_digest,
        "retrieval_digest": context_digest,
        "binding_fingerprint": binding_fingerprint,
        "constraints_complete": binding_fingerprint != "binding:unavailable" and bool(binding.get("allowed_schemas")),
        "intent": intent.strip(),
        "summary": normalized_summary,
        "confidence": float(confidence) if confidence is not None else None,
        "labels": normalized_labels,
        "ambiguities": normalized_ambiguities,
        "ambiguities_declared": bool(ambiguities_declared),
    }
    return {
        "version": 1,
        "snapshot_id": _sha256_id("snapshot", snapshot_body).removeprefix("snapshot:"),
        **snapshot_body,
    }


# ADD: 统一计算查询意图是否需要人工审批；当前模型路径不再依赖自报 confidence。
def decide_query_approval(
    config: DataQueryServiceAbilityConfig,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """根据 confirmation_mode 计算查询意图的审批策略。"""
    confidence = snapshot.get("confidence")
    confidence_ok = confidence is None or (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and float(confidence) >= config.min_auto_confidence
    )
    ambiguities = snapshot.get("ambiguities")
    complete = (
        isinstance(snapshot.get("snapshot_id"), str)
        and isinstance(snapshot.get("binding_fingerprint"), str)
        and snapshot.get("binding_fingerprint") != "binding:unavailable"
        and snapshot.get("constraints_complete") is True
        and isinstance(snapshot.get("labels"), list)
        and bool(snapshot.get("labels"))
        and snapshot.get("ambiguities_declared") is True
        and confidence_ok
        and isinstance(ambiguities, list)
        and not ambiguities
    )
    required = config.confirmation_mode == "always" or (
        config.confirmation_mode == "on_ambiguity" and not complete
    )
    if config.confirmation_mode == "always":
        reason = "confirmation_mode=always，必须调用 ask_intent_approval。"
    elif config.confirmation_mode == "on_ambiguity" and not complete:
        reason = "存在未消解歧义或快照未满足自动放行条件，需要人类审批。"
    else:
        reason = "当前查询可由模型继续判断是否请求人类审批。"
    return {
        "version": 1,
        "snapshot_id": snapshot.get("snapshot_id"),
        "required": required,
        "mode": config.confirmation_mode,
        "reason": reason,
    }


# ADD: 为每个结构化 ambiguity 生成稳定审核项，前端逐项确认时不能依赖数组下标作为持久化身份。
def build_query_review_items(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """从标签快照生成有限长度的逐项审核合同。"""
    snapshot_id = snapshot.get("snapshot_id")
    ambiguities = snapshot.get("ambiguities")
    if not isinstance(snapshot_id, str) or not isinstance(ambiguities, list):
        return []
    items: list[dict[str, Any]] = []
    for index, question in enumerate(ambiguities[:20]):
        if not isinstance(question, str) or not question.strip():
            continue
        item_id = _sha256_id("ambiguity", f"{snapshot_id}\n{index}\n{question.strip()}").removeprefix("ambiguity:")
        items.append(
            {
                "id": item_id,
                "question": question.strip(),
                "status": "pending",
                "options": [
                    {"id": "accept", "label": "按当前理解继续", "value": "accept"},
                    {"id": "modify", "label": "修改这一项", "value": "modify"},
                ],
            }
        )
    return items


# ADD: 复用 DeerFlow human-input v1 请求并把 snapshot 绑定保留在服务端 artifact。
def build_query_approval_request(snapshot: Mapping[str, Any], *, tool_call_id: str) -> dict[str, Any]:
    """构造 DataAgent 查询意图审批请求。"""
    snapshot_id = snapshot.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("确认请求缺少 snapshot_id。")
    request_digest = sha256(f"{snapshot_id}\n{tool_call_id}".encode()).hexdigest()[:24]
    ambiguities = snapshot.get("ambiguities") if isinstance(snapshot.get("ambiguities"), list) else []
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), str) else "请确认当前查询意图。"
    context = "；".join(str(item) for item in ambiguities) if ambiguities else None
    review_items = build_query_review_items(snapshot)
    return {
        "version": 1,
        "kind": "human_input_request",
        "source": "ask_intent_approval",
        "request_id": f"data-query:{request_digest}",
        "tool_call_id": tool_call_id,
        "snapshot_id": snapshot_id,
        "title": "确认数据库查询意图",
        "question": summary,
        "context": context,
        "input_mode": "choice_with_other",
        "options": [
            {"id": "execute", "label": "确认并执行", "value": "execute"},
            {"id": "sql_only", "label": "仅生成 SQL", "value": "sql_only"},
            {"id": "cancel", "label": "取消查询", "value": "cancel"},
        ],
        "review_items": review_items,
    }
