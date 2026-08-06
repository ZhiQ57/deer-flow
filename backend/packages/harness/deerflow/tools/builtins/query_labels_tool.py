"""DeerFlow 查询标签声明工具。"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field


class QueryLabelInput(BaseModel):
    """模型声明的单个用户意图标签。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100, description="标签类型，例如指标、时间、地区、业务口径。")
    source: Literal["user", "database", "derived"] = Field(description="标签来源：user-用户原文、database-数据库真实值、derived-基于上下文证据推导")
    # ADD: 可选保留模型在普通工具上下文中看到的证据引用.
    evidence_refs: list[str] = Field(default_factory=list, max_length=20, description="[可选]推理证据引用, 格式必须是: `[数字]. 证据源`")


@tool("publish_query_labels", parse_docstring=True)
def publish_query_labels_tool(
    intent: str,
    labels: list[QueryLabelInput],
    summary: str | None = None,
    approval_required: bool = False,
) -> str:
    """该工具发布用户当前问题的意图标签.
    目的: 识别用户真实意图, 也许用户的原始问题中没有明确表达, 也许模型在检索结果中发现了更准确的标签, 也许模型在推理过程中发现了更合理的意图实体标签.
    结果: 负责把判断出的意图和标签完整的展示给用户, 可以是用户问题的直接实体、数据库检索出的真实值或推导出的标签, 总之必须完整澄清意图.
    
    何时使用:
    1. 读取最近一轮对话和当前用户请求, 判断是否需要发布意图和标签.
    2. 如果需要或者用户意图已经变化，则重新调用本工具发布标签展示给用户.

    注意:
    1. `source`: 数据库检索到的真实值必须设置为 database, 用户原文必须设置为 user, 推导出的标签必须设置为 derived.
    2. `evidence_refs`: 仅在 source=derived 时才需要提供, 用于说明推导出的标签是基于哪些证据得出的, 这些证据必须是模型可见的工具上下文中的内容.

    Args:
        intent: 当前用户问题的意图标题, 例如: 查询业务口径、聚合统计等, 请按照实际问题发布正确的意图标题.
        labels: 当前完整标签数组，包含标签类型、展示值、来源及可选标准化值和 Evidence refs。
        summary: [可选]中文意图摘要，向用户解释系统当前如何理解问题.
        approval_required: [可选]是否需要用户确认意图和标签.默认 false
    
    Returns:
        固定占位结果；实际标签发布由 middleware 拦截完成
    """
    return "Query labels are processed by middleware."
