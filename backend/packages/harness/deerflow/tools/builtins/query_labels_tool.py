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
    evidence: str | None = Field(default=None, max_length=500, description="数据库来源标签必须填写的 TableRAG Evidence 摘要。")


@tool("publish_query_labels", parse_docstring=True)
def publish_query_labels_tool(
    intent: str,
    labels: list[QueryLabelInput],
    summary: str | None = None,
) -> str:
    """发布当前数据问题的用户意图标签。

    该工具只负责把 lead-agent 已经判断出的意图和标签展示给用户，不执行实体抽取，
    不请求额外模型，也不作为 TableRAG、SQL 校验或 SQL 执行的前置门禁。

    可以在理解用户问题后先发布显式标签，也可以在 TableRAG 或 SQL 执行获得真实
    数据库值后再次调用。每次调用都应提交当前完整标签快照，后一次调用会替换前一次。

    数据库来源标签必须将 source 设置为 database，并填写对应的 TableRAG Evidence
    摘要；不得把未经检索确认的猜测标记为数据库真实值。

    Args:
        intent: lead-agent 当前确认的用户意图，例如 ranking、trend、aggregation 或 detail。
        labels: 当前完整标签数组，包含标签类型、展示值、来源及可选标准化值和 Evidence。
        summary: 可选的中文意图摘要，用于向用户解释系统当前如何理解问题。

    Returns:
        固定占位结果；实际标签发布由 DataAgent 标签 middleware 拦截完成。
    """
    return "Query labels are processed by middleware."
