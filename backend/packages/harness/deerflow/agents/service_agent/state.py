"""DataAgent 检索、标签快照和确认状态合同。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from .config import DataQueryServiceAbilityConfig
from .sqlrag_contract import SQLRAG_SINGLE_ROUTE_COLLECTIONS, is_sqlrag_retrieval_tool_name, require_sqlrag_operation

_RETRIEVAL_COLLECTIONS = {
    "evidences": "evidence",
    "tables": "table",
    "columns": "column",
    "values": "value",
    "join_graphs": "join_graph",
}
_LABEL_SOURCES = frozenset({"user", "database", "derived"})


def _canonical_json(value: object) -> str:
    """生成稳定 JSON 文本。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_id(prefix: str, value: object) -> str:
    """生成带类型前缀的稳定摘要标识。"""
    digest = sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}:sha256:{digest}"


def _stable_ref_record(kind: str, record: Mapping[str, Any], data_source_id: str) -> dict[str, Any]:
    """提取用于生成对象 ref 的稳定字段。"""
    excluded = {"score", "source_scores", "rank", "retrieved_at", "timestamp", "ref"}
    stable = {key: value for key, value in record.items() if key not in excluded}
    return {
        "kind": kind,
        "data_source_id": data_source_id,
        "record": stable,
    }


def _normalize_collection(
    value: object,
    *,
    collection_name: str,
    kind: str,
    data_source_id: str,
) -> list[dict[str, Any]]:
    """规范化一个 TableRAG 结果集合并生成服务端 ref。"""
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"TableRAG result.{collection_name} 必须是数组。")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value[:100]):
        if not isinstance(item, Mapping):
            raise ValueError(f"TableRAG result.{collection_name}[{index}] 必须是对象。")
        record = dict(item)
        record["ref"] = _sha256_id(kind, _stable_ref_record(kind, record, data_source_id))
        normalized.append(record)
    return normalized


# ADD: 将 TableRAG 工具结果登记为服务端可信检索上下文和 Evidence registry。
def build_retrieval_context(
    payload: Mapping[str, Any],
    *,
    tool_name: str,
    turn_id: str,
    data_source_id: str,
    binding: Mapping[str, Any] | None = None,
    request_args: object = None,
) -> dict[str, Any]:
    """构造当前用户轮次的 TableRAG 检索上下文。

    Args:
        payload: TableRAG MCP 工具返回的 JSON 对象。
        tool_name: 实际注册的 MCP 工具名。
        turn_id: 当前可见用户消息 ID。
        data_source_id: service ability 绑定的数据源 ID。
        binding: 服务端解析的无密钥 DataSourceBindingV1。
        request_args: 当前 MCP 工具调用参数，用于登记 query 或 queries。

    Returns:
        带稳定 ref、registry 和 retrieval_digest 的检索上下文。

    Raises:
        ValueError: 工具失败、结果为空或结构不合法。
    """
    if not is_sqlrag_retrieval_tool_name(tool_name):
        raise ValueError("DataAgent 只接受名称严格等于 sqlrag_retrieve 的 MCP 检索结果。")
    if payload.get("ok") is not True:
        raise ValueError("TableRAG 检索未成功，不能登记为当前查询 Evidence。")
    operation = require_sqlrag_operation(payload.get("operation"))
    normalized_args = request_args if isinstance(request_args, Mapping) else {}
    raw_result = payload.get("result")
    if isinstance(raw_result, Sequence) and not isinstance(raw_result, (str, bytes, bytearray)):
        # ADD: 单路 SQLRAG 操作返回数组时按显式 operation 归档到统一 registry。
        route = SQLRAG_SINGLE_ROUTE_COLLECTIONS.get(operation)
        if route is None:
            raise ValueError("TableRAG 单路结果缺少可识别的检索类型。")
        raw_result = {route: list(raw_result)}
    if not isinstance(raw_result, Mapping):
        raise ValueError("TableRAG 成功响应缺少 result 对象。")

    normalized: dict[str, list[dict[str, Any]]] = {}
    registry: dict[str, dict[str, Any]] = {}
    for collection_name, kind in _RETRIEVAL_COLLECTIONS.items():
        items = _normalize_collection(
            raw_result.get(collection_name),
            collection_name=collection_name,
            kind=kind,
            data_source_id=data_source_id,
        )
        normalized[collection_name] = items
        for item in items:
            registry[item["ref"]] = {
                "kind": kind,
                "data_source_id": data_source_id,
                "record": item,
            }

    if not registry:
        raise ValueError("TableRAG 检索没有返回可登记的 Evidence、表、列、值或 Join Graph。")

    request_query = normalized_args.get("query")
    query = raw_result.get("query")
    if not isinstance(query, str) or not query.strip():
        query = request_query.strip() if isinstance(request_query, str) and request_query.strip() else None
    request_queries = normalized_args.get("queries")
    keyword_queries = [item.strip() for item in request_queries if isinstance(item, str) and item.strip()] if isinstance(request_queries, Sequence) and not isinstance(request_queries, (str, bytes, bytearray)) else []
    digest_payload = {
        "data_source_id": data_source_id,
        "operation": operation,
        "query": query,
        "keyword_queries": keyword_queries,
        "collections": normalized,
    }
    return {
        "version": 1,
        "ok": True,
        "turn_id": turn_id,
        "data_source_id": data_source_id,
        "tool_name": tool_name,
        "operation": operation,
        "binding": dict(binding or {}),
        "query": query,
        "keyword_queries": keyword_queries,
        **normalized,
        "registry": registry,
        "retrieval_digest": _sha256_id("retrieval", digest_payload),
        "metadata": dict(raw_result.get("metadata") or {}) if isinstance(raw_result.get("metadata"), Mapping) else {},
    }


# ADD: 合并同一用户轮次的补充检索，保留先前 Evidence 并生成新的服务端摘要。
def merge_retrieval_contexts(
    existing: Mapping[str, Any],
    supplemental: Mapping[str, Any],
) -> dict[str, Any]:
    """合并同数据源、同轮次的两个 TableRAG 检索上下文。

    Args:
        existing: 当前活动快照中的检索上下文。
        supplemental: 新一次补充检索上下文。

    Returns:
        去重合并并重新计算 digest 的检索上下文。

    Raises:
        ValueError: 两次检索不属于同一数据源、轮次或执行目标。
    """
    identity_fields = ("version", "turn_id", "data_source_id")
    if existing.get("ok") is not True or supplemental.get("ok") is not True:
        raise ValueError("只能合并成功的 TableRAG 检索上下文。")
    if any(existing.get(field) != supplemental.get(field) for field in identity_fields):
        raise ValueError("补充检索与当前快照的数据源或用户轮次不一致。")
    existing_binding = existing.get("binding")
    supplemental_binding = supplemental.get("binding")
    if not isinstance(existing_binding, Mapping) or not isinstance(supplemental_binding, Mapping):
        raise ValueError("补充检索缺少数据源绑定。")
    fingerprint = existing_binding.get("binding_fingerprint")
    if not isinstance(fingerprint, str) or supplemental_binding.get("binding_fingerprint") != fingerprint:
        raise ValueError("补充检索与当前服务端数据源绑定不一致。")

    merged_collections: dict[str, list[dict[str, Any]]] = {}
    registry: dict[str, dict[str, Any]] = {}
    for collection_name, kind in _RETRIEVAL_COLLECTIONS.items():
        by_ref: dict[str, dict[str, Any]] = {}
        for context in (existing, supplemental):
            items = context.get(collection_name)
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
                continue
            for item in items:
                if not isinstance(item, Mapping) or not isinstance(item.get("ref"), str):
                    raise ValueError(f"补充检索的 {collection_name} 包含无效 ref。")
                by_ref[str(item["ref"])] = dict(item)
        merged_items = list(by_ref.values())[:100]
        merged_collections[collection_name] = merged_items
        for item in merged_items:
            registry[item["ref"]] = {
                "kind": kind,
                "data_source_id": supplemental["data_source_id"],
                "record": item,
            }

    queries = [query for query in (existing.get("query"), supplemental.get("query")) if isinstance(query, str) and query.strip()]
    queries = list(dict.fromkeys(queries))
    operations = [operation for operation in (existing.get("operation"), supplemental.get("operation")) if isinstance(operation, str) and operation]
    operations = list(dict.fromkeys(operations))
    keyword_queries: list[str] = []
    for context in (existing, supplemental):
        values = context.get("keyword_queries")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
            keyword_queries.extend(item for item in values if isinstance(item, str) and item)
    keyword_queries = list(dict.fromkeys(keyword_queries))
    digest_payload = {
        "data_source_id": supplemental["data_source_id"],
        "operations": operations,
        "queries": queries,
        "keyword_queries": keyword_queries,
        "collections": merged_collections,
    }
    return {
        "version": 1,
        "ok": True,
        "turn_id": supplemental["turn_id"],
        "data_source_id": supplemental["data_source_id"],
        "tool_name": supplemental.get("tool_name"),
        "operation": supplemental.get("operation"),
        "operations": operations,
        "binding": dict(supplemental_binding),
        "query": supplemental.get("query") or existing.get("query"),
        "queries": queries,
        "keyword_queries": keyword_queries,
        **merged_collections,
        "registry": registry,
        "retrieval_digest": _sha256_id("retrieval", digest_payload),
        "metadata": dict(supplemental.get("metadata") or {}) if isinstance(supplemental.get("metadata"), Mapping) else {},
    }


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


def _normalize_label(item: Mapping[str, Any], *, index: int, registry: Mapping[str, Any]) -> dict[str, Any]:
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
    if source == "database" and not evidence_refs:
        raise ValueError(f"labels[{index}] 的数据库来源标签必须引用 Evidence。")
    missing_refs = [ref for ref in evidence_refs if ref not in registry]
    if missing_refs:
        raise ValueError(f"labels[{index}] 引用了当前检索中不存在的 Evidence：{missing_refs}")

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
    retrieval: Mapping[str, Any],
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
    if retrieval.get("ok") is not True or retrieval.get("data_source_id") != data_source_id:
        raise ValueError("标签快照的数据源与当前 TableRAG 检索上下文不一致。")
    retrieval_digest = retrieval.get("retrieval_digest")
    registry = retrieval.get("registry")
    binding = retrieval.get("binding")
    if not isinstance(retrieval_digest, str) or not isinstance(registry, Mapping):
        raise ValueError("当前 TableRAG 检索上下文缺少 digest 或 registry。")
    if not isinstance(binding, Mapping) or binding.get("data_source_id") != data_source_id:
        raise ValueError("当前 TableRAG 检索上下文缺少有效数据源绑定。")
    binding_fingerprint = binding.get("binding_fingerprint")
    if not isinstance(binding_fingerprint, str):
        raise ValueError("TableRAG 检索与 SQL 执行缺少服务端数据源绑定摘要。")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("intent 不能为空。")
    if not labels or len(labels) > 30:
        raise ValueError("labels 必须包含 1 到 30 项。")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1):
        raise ValueError("confidence 必须位于 0 到 1。")
    if len(ambiguities) > 20 or any(not isinstance(item, str) or not item.strip() for item in ambiguities):
        raise ValueError("ambiguities 必须是最多 20 项的非空字符串数组。")

    normalized_labels = [_normalize_label(item, index=index, registry=registry) for index, item in enumerate(labels)]
    normalized_summary = summary.strip() if isinstance(summary, str) and summary.strip() else None
    normalized_ambiguities = [item.strip() for item in ambiguities]
    snapshot_body = {
        "turn_id": turn_id,
        "data_source_id": data_source_id,
        "ability_version": ability_version,
        "retrieval_digest": retrieval_digest,
        "binding_fingerprint": binding_fingerprint,
        "constraints_complete": bool(binding.get("allowed_schemas")),
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
        and isinstance(snapshot.get("retrieval_digest"), str)
        and isinstance(snapshot.get("binding_fingerprint"), str)
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



