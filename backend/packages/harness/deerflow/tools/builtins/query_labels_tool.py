"""DeerFlow 查询标签声明工具。"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field


class QueryLabelInput(BaseModel):
    """模型声明的单个用户意图标签。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=50, description="标签类型，例如指标、时间、地区、业务口径。")
    value: str = Field(min_length=1, max_length=200, description="向用户展示的标签值。")
    source: Literal["user", "database", "derived"] = Field(description="标签来源：用户原文、数据库真实值或基于证据推导。")
    normalized: str | None = Field(default=None, max_length=200, description="可选的标准化值。")
    # ADD: 数据库标签只接收服务端登记的 Evidence ref，不再接受可伪造的自由文本摘要。
    evidence_refs: list[str] = Field(default_factory=list, max_length=20, description="当前 TableRAG retrieval registry 中的 Evidence/Table/Column/Value ref。")


@tool("publish_query_labels", parse_docstring=True)
def publish_query_labels_tool(
    intent: str,
    labels: list[QueryLabelInput],
    summary: str | None = None,
    ambiguities: list[str] | None = None,
) -> str:
    """发布当前数据问题的用户意图标签。

    该工具只负责把 lead-agent 已经判断出的意图和标签展示给用户，不执行实体抽取，
    不请求额外模型，也不作为 TableRAG、SQL 校验或 SQL 执行的前置门禁。

    每次调用都应提交当前完整标签快照，后一次调用会替换前一次。宿主 Agent 可以
    配置该工具必须在首次有效数据库检索后发布；调用时必须遵守宿主流程的阶段要求。

    数据库来源标签必须将 source 设置为 database，并引用当前 TableRAG registry 中的
    evidence_refs；不得提交自由文本 Evidence 或把未经检索确认的猜测标记为数据库真实值。

    Args:
        intent: lead-agent 当前确认的用户意图，例如 ranking、trend、aggregation 或 detail。
        labels: 当前完整标签数组，包含标签类型、展示值、来源及可选标准化值和 Evidence refs。
        summary: 可选的中文意图摘要，用于向用户解释系统当前如何理解问题。
        ambiguities: 尚未解决且可能改变 SQL 的歧义列表；无歧义时传空数组。

    Returns:
        固定占位结果；实际标签发布由 DataAgent 标签 middleware 拦截完成。
    """
    return "Query labels are processed by middleware."
