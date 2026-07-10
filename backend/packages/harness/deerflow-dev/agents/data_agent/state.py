"""DataAgent 实验性状态定义。"""

from __future__ import annotations

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from deerflow.agents.thread_state import ThreadState

DataAgentStage = Literal[
    "query_context",
    "retrieval_completed",
    "sql_validation_failed",
    "sql_validated",
    "sql_execution_failed",
    "sql_executed",
    "chart_failed",
    "chart_ready",
]


class DataQueryEntity(TypedDict):
    """DataAgent 从用户问题中识别出的实体标签。"""

    label: str
    value: str
    normalized: NotRequired[str]
    source: NotRequired[str]


class DataQueryContext(TypedDict):
    """DataAgent QueryContextMiddleware 产出的结构化查询上下文。"""

    original_query: str
    normalized_query: str
    intent: str
    aliases: list[DataQueryEntity]
    entities: list[DataQueryEntity]
    labels: list[DataQueryEntity]
    warnings: list[str]


class DataRetrievalContext(TypedDict):
    """DataAgent TableRAG 检索阶段摘要。"""

    ok: bool
    tool_name: str
    query: str
    content_sha256: str
    result_preview: str


class DataSQLValidation(TypedDict, total=False):
    """DataAgent SQL 校验结果。"""

    valid: bool
    original_sql: str
    executable_sql: str
    source_sql_sha256: str
    sql_sha256: str
    max_rows: int
    effective_limit: int
    limit_applied: bool
    tables: list[str]
    columns: list[str]
    warnings: list[str]
    error: str


class DataSQLExecution(TypedDict, total=False):
    """DataAgent SQL 执行结果。"""

    ok: bool
    sql_sha256: str
    executable_sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int
    error: str


class DataChartSpec(TypedDict, total=False):
    """DataAgent 图表规格。"""

    type: str
    title: str
    x: str | None
    y: list[str]
    series: str | None
    encoding: dict[str, Any]
    data: list[dict[str, Any]]
    row_count: int
    truncated: bool


def replace_value[T](existing: T | None, new: T | None) -> T | None:
    """替换 DataAgent 单值状态。

    Args:
        existing: 旧值。
        new: 新值。

    Return:
        始终返回新值；只有状态更新显式包含对应键时才会触发该 reducer。
    """
    return new


def merge_retrieval_context(
    existing: DataRetrievalContext | None,
    new: DataRetrievalContext | None,
) -> DataRetrievalContext | None:
    """合并单轮 TableRAG 检索状态。

    并行检索可能同时返回“有候选”和“空结果”。只要本轮已有一次成功召回，
    后续空结果不应把阶段门禁随机回退；显式 None 仍用于新一轮查询重置。

    Args:
        existing: 旧检索上下文。
        new: 新检索上下文。

    Return:
        保留本轮最近成功检索的上下文，或返回新值。
    """
    if new is None:
        return None
    if existing is not None and existing.get("ok") is True and new.get("ok") is not True:
        return existing
    return new


class DataAgentState(ThreadState):
    """DataAgent 状态结构，继承 DeerFlow 原生 ThreadState 并增加生产流程状态。"""

    data_agent_stage: Annotated[DataAgentStage | None, replace_value]
    data_query_context: Annotated[DataQueryContext | None, replace_value]
    data_retrieval_context: Annotated[DataRetrievalContext | None, merge_retrieval_context]
    data_generated_sql: Annotated[str | None, replace_value]
    data_sql_validation: Annotated[DataSQLValidation | None, replace_value]
    data_sql_execution: Annotated[DataSQLExecution | None, replace_value]
    data_last_successful_sql_execution: Annotated[DataSQLExecution | None, replace_value]
    data_chart_spec: Annotated[DataChartSpec | None, replace_value]
