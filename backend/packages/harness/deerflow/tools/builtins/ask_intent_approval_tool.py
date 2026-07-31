"""DataAgent 查询意图审批工具。"""

from __future__ import annotations

from langchain.tools import tool


@tool("ask_intent_approval", parse_docstring=True, return_direct=True)
def ask_intent_approval_tool(
    snapshot_id: str | None = None,
    question: str | None = None,
    context: str | None = None,
    options: list[str] | None = None,
) -> str:
    """Request human approval for the current DataAgent query intent.

    Use this tool after ``publish_query_labels`` when the current query snapshot
    needs explicit human approval before SQL execution or when the model wants
    a human to choose between execute / SQL-only / cancel.

    The middleware will bind the active DataAgent labels snapshot to the
    approval card, pause execution, and keep the approval result in the message
    history as a real tool result so the model can read it on the next step.

    Args:
        snapshot_id: Optional query snapshot identifier. If omitted, the middleware
            uses the active DataAgent snapshot.
        question: Optional approval question override. The middleware may replace
            this with a canonical query-intent prompt.
        context: Optional supporting context. The middleware may merge it with the
            current labels summary and ambiguity list.
        options: Optional approval choices. Defaults to execute / sql_only / cancel.

    Returns:
        Placeholder text; the middleware intercepts the call and performs the
        actual approval request / interruption flow.
    """
    return "Query intent approval request processed by middleware."
