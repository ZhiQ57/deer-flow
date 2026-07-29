import copy
from collections.abc import Mapping, Sequence
from functools import cache
from typing import Annotated, Any, NotRequired, TypedDict, get_type_hints

from langchain.agents import AgentState
from langchain_core.messages import AnyMessage
from langgraph.channels import DeltaChannel
from langgraph.graph.message import add_messages

import deerflow.checkpoint_patches as _checkpoint_patches  # noqa: F401 - import-time saver fixes
from deerflow.agents.goal_state import GoalState
from deerflow.agents.service_agent.data_agent_thread_state import ServiceState, normalize_service_state_version
from deerflow.config.database_config import CheckpointChannelMode
from deerflow.subagents.status_contract import SUBAGENT_STATUS_VALUES


class SandboxState(TypedDict):
    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    """Metadata for a viewed image file.

    Only lightweight metadata is persisted in checkpoint state; the actual
    image bytes are read on-demand from disk when the model needs them.
    This avoids duplicating large base64 payloads across every checkpoint
    (see #4138).
    """

    mime_type: str
    size: int
    actual_path: str


def merge_sandbox(existing: SandboxState | None, new: SandboxState | None) -> SandboxState | None:
    """Reducer for sandbox state - accepts idempotent writes only.

    Multiple sandbox tools can initialize lazily in the same graph step and
    emit the same sandbox_id via Command(update=...). LangGraph needs an
    explicit reducer for that shared state key. Different sandbox ids in the
    same thread indicate a lifecycle/isolation bug, so fail closed instead of
    choosing one silently.
    """
    if new is None:
        return existing
    if existing is None:
        return new

    existing_id = existing.get("sandbox_id")
    new_id = new.get("sandbox_id")
    if existing_id == new_id:
        return existing
    raise ValueError(f"Conflicting sandbox state updates: {existing_id!r} != {new_id!r}")


SandboxStateField = Annotated[NotRequired[SandboxState | None], merge_sandbox]


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Reducer for artifacts list - merges and deduplicates artifacts."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    # Use dict.fromkeys to deduplicate while preserving order
    return list(dict.fromkeys(existing + new))


def merge_viewed_images(existing: dict[str, ViewedImageData] | None, new: dict[str, ViewedImageData] | None) -> dict[str, ViewedImageData]:
    """Reducer for viewed_images dict - merges image dictionaries.

    Special case: If new is an empty dict {}, it clears the existing images.
    This allows middlewares to clear the viewed_images state after processing.
    """
    if existing is None:
        return new or {}
    if new is None:
        return existing
    # Special case: empty dict means clear all viewed images
    if len(new) == 0:
        return {}
    # Merge dictionaries, new values override existing ones for same keys
    return {**existing, **new}


def merge_todos(existing: list | None, new: list | None) -> list | None:
    """Reducer for todos list - keeps the last non-None value.

    Semantics:
    - If `new` is None (node didn't touch todos), preserve `existing`.
    - If `new` is provided (even empty list), it represents an explicit
      update and wins over `existing`.
    """
    if new is None:
        return existing
    return new


def merge_goal(existing: GoalState | None, new: GoalState | None) -> GoalState | None:
    """Reducer for goal state - preserves existing when a node does not touch it."""
    if new is None:
        return existing
    return new


class PromotedTools(TypedDict):
    catalog_hash: str
    names: list[str]


def merge_promoted(existing: PromotedTools | None, new: PromotedTools | None) -> PromotedTools | None:
    """Reducer for deferred-tool promotions, scoped by catalog hash.

    - new None/empty -> preserve existing (node didn't touch promotions).
    - catalog_hash changed -> replace wholesale, dropping stale names (prevents a
      persisted bare name from exposing a different tool after catalog drift).
    - same catalog_hash -> union names, dedupe, preserve order.
    """
    if not new:
        return existing
    if existing is None or existing.get("catalog_hash") != new["catalog_hash"]:
        return {
            "catalog_hash": new["catalog_hash"],
            "names": list(dict.fromkeys(new["names"])),
        }
    return {
        "catalog_hash": existing["catalog_hash"],
        "names": list(dict.fromkeys(existing["names"] + new["names"])),
    }


TERMINAL_STATUSES: frozenset[str] = frozenset(SUBAGENT_STATUS_VALUES)
_DELEGATION_LEDGER_MAX_ENTRIES = 50


class DelegationEntry(TypedDict):
    id: str
    run_id: NotRequired[str]
    description: str
    subagent_type: str
    status: str
    result_brief: NotRequired[str]
    result_sha256: NotRequired[str]
    result_ref: NotRequired[str]
    # Why a guardrail cap ended the run early (#3875 Phase 2): token_capped /
    # turn_capped / loop_capped. The status stays completed/failed; this field
    # is the additive signal that distinguishes a capped run from a clean one.
    stop_reason: NotRequired[str]
    created_at: str


def merge_delegations(existing: list[DelegationEntry] | None, new: list[DelegationEntry] | None) -> list[DelegationEntry]:
    """Reducer for the delegation ledger.

    - new None/empty -> preserve existing.
    - append entries, replacing same id with the latest version while preserving
      first-seen order.
    - terminal status is never overwritten by a non-terminal status.
    """
    if not new:
        return existing or []

    by_id: dict[str, DelegationEntry] = {}
    order: list[str] = []
    for entry in [*(existing or []), *new]:
        entry_id = entry["id"]
        previous = by_id.get(entry_id)
        if previous is not None and previous["status"] in TERMINAL_STATUSES and entry["status"] not in TERMINAL_STATUSES:
            continue
        if entry_id not in by_id:
            order.append(entry_id)
        elif previous.get("created_at"):
            entry = {**entry, "created_at": previous["created_at"]}
            if previous.get("run_id") and not entry.get("run_id"):
                entry["run_id"] = previous["run_id"]
        by_id[entry_id] = entry
    merged = [by_id[entry_id] for entry_id in order]
    if len(merged) > _DELEGATION_LEDGER_MAX_ENTRIES:
        merged = merged[-_DELEGATION_LEDGER_MAX_ENTRIES:]
    return merged


_SKILL_CONTEXT_MAX_ENTRIES = 8
_SKILL_DESCRIPTION_MAX_CHARS = 500


class SkillEntry(TypedDict):
    name: str
    path: str
    description: str
    loaded_at: int


def _normalize_skill_entry(entry: Mapping[str, object]) -> SkillEntry:
    """Drop legacy payload keys before storing skill_context back to state."""
    description = entry.get("description")
    loaded_at = entry.get("loaded_at")
    return {
        "name": str(entry.get("name") or ""),
        "path": str(entry["path"]),
        "description": " ".join(description.split())[:_SKILL_DESCRIPTION_MAX_CHARS] if isinstance(description, str) else "",
        "loaded_at": loaded_at if isinstance(loaded_at, int) else 0,
    }


def merge_skill_context(existing: list[SkillEntry] | None, new: list[SkillEntry] | None) -> list[SkillEntry]:
    """Reducer for the skill-context channel.

    - new None/empty -> preserve existing.
    - legacy entries are normalized to references; verbatim body keys are dropped.
    - dedup by ``path``; later reads refresh recency and replace the reference.
    - cap by keeping the most recently read entries. ``loaded_at`` is
      observational only because message indices reset after compaction.
    """
    normalized_existing = [_normalize_skill_entry(entry) for entry in existing or []]
    if not new:
        return normalized_existing

    by_path: dict[str, SkillEntry] = {}
    order: list[str] = []
    for entry in normalized_existing:
        path = entry["path"]
        if path not in by_path:
            order.append(path)
        by_path[path] = entry

    for entry in (_normalize_skill_entry(entry) for entry in new):
        path = entry["path"]
        if path in by_path:
            order.remove(path)
        order.append(path)
        by_path[path] = entry

    merged = [by_path[path] for path in order]
    if len(merged) > _SKILL_CONTEXT_MAX_ENTRIES:
        merged = merged[-_SKILL_CONTEXT_MAX_ENTRIES:]
    return merged


_SERVICE_STATE_MAX_ENTRIES = 16

_DATA_QUERY_STAGE_RANK = {
    "idle": 0,
    "retrieving": 1,
    "needs_refinement": 1,
    "labels_published": 2,
    "awaiting_confirmation": 2,
    "approved": 3,
    "sql_generating": 4,
    "sql_validating": 5,
    "sql_ready": 6,
    "executing": 7,
    "succeeded": 8,
    "failed": 8,
    "cancelled": 8,
}
_DATA_QUERY_TERMINAL_STAGES = frozenset({"succeeded", "failed", "cancelled", "unsupported_version"})


# ADD: 对 DataAgent 活动快照执行单向状态机合并，丢弃旧轮次、旧 snapshot 和阶段回退写入。
def _can_replace_data_query_state(current: Mapping[str, object], incoming: Mapping[str, object]) -> bool:
    """判断新的 DataAgent 服务状态能否替换当前活动快照。"""
    if current.get("version") != 1 or incoming.get("version") != 1:
        return True
    current_turn = current.get("turn_id")
    incoming_turn = incoming.get("turn_id")
    incoming_stage = incoming.get("stage")
    current_stage = current.get("stage")
    if isinstance(current_turn, str) and isinstance(incoming_turn, str) and current_turn != incoming_turn:
        # 新用户问题不再依赖单独的 turn-reset middleware 先写 idle；
        # TableRAG 检索可以直接开启新快照，但旧轮 SQL/审批结果仍不能覆盖当前轮。
        return incoming_stage in {"idle", "retrieving", "needs_refinement"}

    current_snapshot = current.get("snapshot_id")
    incoming_snapshot = incoming.get("snapshot_id")
    if current_snapshot == incoming_snapshot:
        if current_stage in _DATA_QUERY_TERMINAL_STAGES:
            return incoming_stage == current_stage
        current_rank = _DATA_QUERY_STAGE_RANK.get(str(current_stage))
        incoming_rank = _DATA_QUERY_STAGE_RANK.get(str(incoming_stage))
        if current_rank is None or incoming_rank is None:
            return incoming_stage == current_stage
        return incoming_rank >= current_rank

    if incoming_stage in {"retrieving", "needs_refinement"}:
        return current_stage in {"idle", "retrieving", "needs_refinement", "labels_published", "awaiting_confirmation"}
    if incoming_stage in {"labels_published", "awaiting_confirmation", "approved", "cancelled"}:
        return current_stage in {"retrieving", "labels_published"}
    return current_snapshot is None


# ADD: 按 service_name 替换活动快照，避免 dict 不可哈希和状态无界增长。
def merge_service_states(existing: list[ServiceState] | None, new: list[ServiceState] | None) -> list[ServiceState]:
    """合并服务状态快照并保持每个服务只有一个活动状态。

    Args:
        existing: checkpoint 中的现有服务状态。
        new: 当前 LangGraph step 产生的服务状态更新。

    Returns:
        按最近更新时间排列且有界的活动服务状态列表。

    Raises:
        TypeError: 更新项不是映射对象。
        ValueError: 快照缺少 service_name。
    """
    active: dict[str, ServiceState] = {}
    order: list[str] = []

    def _key(item: Mapping[str, object]) -> str:
        service_name = item.get("service_name")
        if isinstance(service_name, str) and service_name.strip():
            return service_name.strip()
        # ADD: 兼容历史展示状态，避免升级时静默丢失旧卡片数据。
        label = item.get("label")
        if isinstance(label, str) and label.strip():
            return f"legacy:{label.strip()}"
        raise ValueError("service state 必须包含非空 service_name。")

    def _apply(item: ServiceState) -> None:
        if not isinstance(item, Mapping):
            raise TypeError("service state 更新项必须是对象。")
        key = _key(item)
        if item.get("clear") is True:
            active.pop(key, None)
            if key in order:
                order.remove(key)
            return
        # ADD: DataAgent 未知状态版本在 reducer 入口安全降级，避免历史 checkpoint 无法读取或恢复旧授权。
        normalized = normalize_service_state_version(item)
        normalized.pop("clear", None)
        current = active.get(key)
        if key == "data_query" and isinstance(current, Mapping) and not _can_replace_data_query_state(current, normalized):
            return
        if key in order:
            order.remove(key)
        order.append(key)
        active[key] = normalized

    for item in existing or []:
        _apply(item)
    for item in new or []:
        _apply(item)

    if len(order) > _SERVICE_STATE_MAX_ENTRIES:
        order = order[-_SERVICE_STATE_MAX_ENTRIES:]
    return [active[key] for key in order]


class ThreadState(AgentState):
    sandbox: SandboxStateField
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    # ADD: 定制化业务字段
    service_states: Annotated[list[ServiceState], merge_service_states]
    todos: Annotated[list | None, merge_todos]
    goal: Annotated[GoalState | None, merge_goal]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # image_path -> metadata (no base64)
    promoted: Annotated[PromotedTools | None, merge_promoted]
    delegations: Annotated[list[DelegationEntry], merge_delegations]
    skill_context: Annotated[list[SkillEntry], merge_skill_context]
    summary_text: NotRequired[str | None]


def merge_message_writes(state: list[AnyMessage], writes: Sequence[Any]) -> list[AnyMessage]:
    result = list(state)
    for write in writes:
        result = list(add_messages(result, write))
    return result


DELTA_MESSAGES_FIELD = Annotated[
    list[AnyMessage],
    DeltaChannel(merge_message_writes, snapshot_frequency=1000),
]


class DeltaThreadState(ThreadState):
    messages: DELTA_MESSAGES_FIELD


THREAD_STATE_REDUCER_FIELDS = frozenset(
    {
        "messages",
        "sandbox",
        "artifacts",
        "todos",
        "goal",
        "viewed_images",
        "promoted",
        "delegations",
        "skill_context",
    }
)


def get_thread_state_schema(mode: CheckpointChannelMode) -> type:
    return DeltaThreadState if mode == "delta" else ThreadState


@cache
def adapt_state_schema_for_mode(schema: type, mode: CheckpointChannelMode) -> type:
    if mode == "full":
        return schema
    annotations = get_type_hints(schema, include_extras=True)
    annotations["messages"] = DELTA_MESSAGES_FIELD
    return TypedDict(
        f"Delta{schema.__module__.replace('.', '_')}_{schema.__name__}",
        annotations,
        total=getattr(schema, "__total__", True),
    )


def normalize_middleware_state_schemas(middleware: Sequence[Any], mode: CheckpointChannelMode) -> list[Any]:
    if mode == "full":
        return list(middleware)
    normalized = []
    for item in middleware:
        schema = getattr(item, "state_schema", None)
        if schema is None:
            normalized.append(item)
            continue
        adapted = copy.copy(item)
        adapted.state_schema = adapt_state_schema_for_mode(schema, mode)
        normalized.append(adapted)
    return normalized
