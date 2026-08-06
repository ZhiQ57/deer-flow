"""Gateway SQL SubAgent 工具提供器。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
from fastapi import FastAPI
from langchain_core.tools import BaseTool, StructuredTool

from app.gateway.internal_auth import create_internal_auth_headers
from deerflow.subagents.tool_provider import (
    SubagentToolContext,
    register_subagent_tool_provider,
)

from .runtime_registry import sql_execution_runtime_registry

_PROVIDER_NAME = "gateway-sql-execution"

# ADD
def _data_query_sql_request_from_prompt(prompt: str | None) -> dict[str, Any] | None:
    """从 SQL SubAgent 的任务 prompt 读取 flow_id 和执行动作。"""
    if not isinstance(prompt, str) or not prompt.strip():
        return None

    try:
        parsed = json.loads(prompt)
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(parsed, dict) or parsed.get("kind") != "data_query_sql_request":
        return None

    flow_id = parsed.get("flow_id")
    action = parsed.get("action")
    if not isinstance(flow_id, str) or not flow_id.strip():
        return None
    if action not in {"execute", "sql_only"}:
        return None

    request_payload = dict(parsed)
    request_payload["flow_id"] = flow_id.strip()
    return request_payload


def _gateway_failure(
    *,
    flow_id: str,
    data_source_id: str,
    error_code: str,
) -> dict[str, Any]:
    """构造 Gateway 调用失败的安全工具结果。

    Args:
        flow_id: 当前 Flow。
        data_source_id: 当前数据源标识。
        error_code: 安全错误码。

    Returns:
        可写入 ToolMessage artifact 的失败合同。
    """
    return {
        "version": 1,
        "kind": "data_query_sql_result",
        "flow_id": flow_id,
        "data_source_id": data_source_id,
        "validation": {
            "version": 1,
            "valid": False,
            "error_code": error_code,
            "flow_id": flow_id,
        },
        "execution": None,
    }


class GatewaySqlSubagentToolProvider:
    """通过 Gateway 内部 API 构建 SQL SubAgent 工具。"""

    def __init__(self, app: FastAPI) -> None:
        """初始化工具提供器。

        Args:
            app: 当前 Gateway FastAPI 应用。
        """
        self._app = app

    async def _post(
        self,
        *,
        path: str,
        context: SubagentToolContext,
        flow_id: str,
        sql: str,
        data_source_id: str,
        validation_digest: str | None = None,
    ) -> dict[str, Any]:
        """调用 Gateway 内部 SQL API。

        Args:
            path: 内部 API 路径。
            context: 子代理可信运行上下文。
            flow_id: 当前 Flow。
            sql: SQL SubAgent 生成的候选 SQL。
            data_source_id: 当前数据源标识。
            validation_digest: 可选的 Gateway SQL 校验摘要。

        Returns:
            Gateway 返回的权威 SQL 合同；请求失败时返回安全失败合同。
        """
        if not all((context.thread_id, context.run_id, context.user_id, context.agent_name)):
            return _gateway_failure(
                flow_id=flow_id,
                data_source_id=data_source_id,
                error_code="SQL_GATEWAY_CONTEXT_MISSING",
            )
        transport = httpx.ASGITransport(app=self._app)
        try:
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://gateway.internal",
                timeout=httpx.Timeout(60.0),
            ) as client:
                request_body = {
                    "agent_name": context.agent_name,
                    "sql": sql,
                    "flow_id": flow_id,
                    "run_id": context.run_id,
                }
                if validation_digest is not None:
                    request_body["validation_digest"] = validation_digest
                response = await client.post(
                    path,
                    headers=create_internal_auth_headers(owner_user_id=context.user_id),
                    json=request_body,
                )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return _gateway_failure(
            flow_id=flow_id,
            data_source_id=data_source_id,
            error_code="SQL_GATEWAY_REQUEST_FAILED",
        )

    def build_tools(self, context: SubagentToolContext) -> list[BaseTool]:
        """为允许的 SQL SubAgent 构建 Gateway 工具。

        Args:
            context: 子代理可信运行上下文。

        Returns:
            当前审批动作允许的 SQL 工具列表。
        """

        ability = context.parent_context.get("data_query_service_ability")
        sql_request = _data_query_sql_request_from_prompt(context.task_prompt)

        if (
            not isinstance(ability, Mapping)
            or ability.get("type") != "data_query"
            or ability.get("version") != 1
            or ability.get("enable_sql_rag") is not True
            or ability.get("sql_execution_enabled") is not True
            or not isinstance(ability.get("data_source_id"), str)
            or not isinstance(ability.get("sql_subagent_name"), str)
            or context.parent_context.get("data_query_sql_subagent_allowed") is not True
            or context.subagent_type != ability.get("sql_subagent_name")
            or sql_request is None
            or not all((context.thread_id, context.run_id, context.user_id, context.agent_name))
        ):
            return []

        action = sql_request["action"]
        flow_id = sql_request["flow_id"]

        request_data_source_id = sql_request.get("data_source_id")
        if request_data_source_id is not None and request_data_source_id != ability.get("data_source_id"):
            return []

        if not sql_execution_runtime_registry.authorize_flow(
            run_id=context.run_id,
            thread_id=context.thread_id,
            user_id=context.user_id,
            agent_name=context.agent_name,
            flow_id=flow_id,
        ):
            return []

        async def validate(sql: str) -> tuple[str, dict[str, Any]]:
            """通过 Gateway 校验候选 SQL。

            Args:
                sql: SQL SubAgent 生成的候选 SQL。

            Returns:
                模型可读 JSON 和权威 artifact。
            """
            result = await self._post(
                path=f"/api/internal/threads/{context.thread_id}/sql/validate",
                context=context,
                flow_id=flow_id,
                sql=sql,
                data_source_id=str(ability["data_source_id"]),
            )
            return json.dumps(result, ensure_ascii=False, sort_keys=True), result

        async def execute(
            sql: str,
            validation_digest: str,
        ) -> tuple[str, dict[str, Any]]:
            """通过 Gateway 校验并执行候选 SQL。

            Args:
                sql: SQL SubAgent 生成或修复后的候选 SQL。
                validation_digest: 最近一次 Gateway 校验返回的摘要。

            Returns:
                模型可读 JSON 和权威 artifact。
            """
            result = await self._post(
                path=f"/api/internal/threads/{context.thread_id}/sql/execute",
                context=context,
                flow_id=flow_id,
                sql=sql,
                data_source_id=str(ability["data_source_id"]),
                validation_digest=validation_digest,
            )
            return json.dumps(result, ensure_ascii=False, sort_keys=True), result

        tools: list[BaseTool] = [
            StructuredTool.from_function(
                coroutine=validate,
                name="data_validate_sql",
                description="通过 Gateway 按当前 approved Flow 校验单条只读 SQL。",
                response_format="content_and_artifact",
            )
        ]
        if action == "execute":
            tools.append(
                StructuredTool.from_function(
                    coroutine=execute,
                    name="data_execute_sql",
                    description="通过 Gateway 校验并执行单条只读 SQL；失败后根据结构化错误修复 SQL 再调用。",
                    response_format="content_and_artifact",
                )
            )
        return tools


def register_gateway_sql_tool_provider(app: FastAPI) -> None:
    """把 Gateway SQL 工具提供器注册到 Harness 通用扩展点。

    Args:
        app: 当前 Gateway FastAPI 应用。

    Returns:
        无返回值。
    """
    register_subagent_tool_provider(
        _PROVIDER_NAME,
        GatewaySqlSubagentToolProvider(app),
        replace=True,
    )
