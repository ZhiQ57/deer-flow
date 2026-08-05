"""DataAgent 查询意图审批工具。"""

from __future__ import annotations

from langchain.tools import tool
from pydantic import BaseModel

class AskQuestionList(BaseModel):
    """模型声明的问题汇总列表"""
    question: str
    options: list[str] | None = None

@tool("ask_intent_approval", parse_docstring=True, return_direct=True)
def ask_intent_approval_tool(
    asklist: list[AskQuestionList],
    context: str | None = None
) -> str:
    """请求人工审批/澄清真实意图.
    遇到必须获取用户 审批/澄清 才能继续的场景时，通常在调用 `publish_query_labels` 工具之后调用本工具.
    
    该工具会被中间件拦截, 中断本次执行并绑定到审批卡片，暂停执行流程.
    
    何时使用:
    - 用户意图存在歧义.
    - 需要人工确认或澄清意图时.
    - 即将执行存在潜在风险的操作.

    Args:
        - asklist: [必填] 向用户提问的问题对象列表, 对象包含 question 和 options 两个字段:
            - question: 问题文本,对有歧义的意图, 向用户提问.
            - options: 问题对应的审批选项列表, 例如: ["确认", "取消", "..."], 请按照实际选项填写, 如果没有选项可使用输入文本框获取用户输入.
        - context: [必填] 说明为什么需要审批/澄清的原因, 帮助用户理解当前情况.
    
    Returns:
        占位文本；中间件会拦截本次调用，实际执行审批请求与流程中断逻辑。
    """
    return "Query intent approval request processed by middleware."
