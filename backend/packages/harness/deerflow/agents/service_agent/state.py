"""DataAgent 检索、标签快照和确认状态合同。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Callable, TypeAlias, TypedDict

from deerflow.agents.service_agent.contracts import ServiceState

from .config import DataQueryServiceAbilityConfig

_LABEL_SOURCES = frozenset({"user", "database", "derived"})



def merge_service_states(
    existing: list[ServiceState] | None,
    updates: list[ServiceState] | None,
) -> list[ServiceState]:
    """根据 SERVICE_ABILITY_REGISTRY 合并业务状态。"""

    # 延迟导入，避免 registry -> data_agent.service_state
    # -> contracts 的导入链影响 ThreadState 初始化。
    from .registry import SERVICE_ABILITY_REGISTRY

    active: dict[str, ServiceState] = {}

    for state in [
        *(existing or []),
        *(updates or []),
    ]:
        if not isinstance(state, Mapping):
            raise TypeError("service state 必须是对象。")

        service_name = state.get("service_name")

        if not isinstance(service_name, str):
            raise ValueError("service state 缺少 service_name。")

        service_name = service_name.strip()

        if not service_name:
            raise ValueError("service_name 不能为空。")

        if state.get("clear") is True:
            active.pop(service_name, None)
            continue

        incoming: ServiceState = {
            **state,
            "service_name": service_name,
        }

        incoming.pop("clear", None)

        version = incoming.get("version")

        spec = None

        if isinstance(version, int) and not isinstance(version, bool):
            spec = SERVICE_ABILITY_REGISTRY.get(service_name)

        merger = spec.state_merger if spec is not None else None

        current = active.get(service_name)

        merged = (
            merger(current, incoming)
            if merger is not None
            else incoming
        )

        if merged is None:
            continue

        active.pop(service_name, None)
        active[service_name] = merged

    return list(active.values())



# def merge_service_states(
#     existing: list[ServiceState] | None,
#     updates: list[ServiceState] | None,
# ) -> list[ServiceState]:
#     """
#     合并 service_states。
#     """
#     # 延迟导入，避免 registry -> data_agent.service_state
#     # -> contracts 的导入链影响 ThreadState 初始化。
#     from .registry import SERVICE_ABILITY_REGISTRY

#     active: dict[str, ServiceState] = {}

#     for state in [*(existing or []), *(updates or [])]:
#         service_name = state["service_name"].strip()

#         if not service_name:
#             raise ValueError("service_name 不能为空")

#         if state.get("clear"):
#             active.pop(service_name, None)
#             continue

#         incoming = {
#             **state,
#             "service_name": service_name,
#         }
#         incoming.pop("clear", None)

#         merge = SERVICE_ABILITY_REGISTRY.get(service_name)
#         merged = (
#             merge(active.get(service_name), incoming)
#             if merge
#             else incoming
#         )

#         if merged is None:
#             continue

#         # 重新插入，使最近更新的业务位于末尾。
#         active.pop(service_name, None)
#         active[service_name] = merged

#     return list(active.values())





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


# ADD: 创建业务活动状态字段
def make_service_state(
    *,
    turn_id: str,
    stage: str,
    payload: Mapping[str, Any] | None = None,
    snapshot_id: str | None = None,
    data_source_id: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """构造 service_states 活动状态"""
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
