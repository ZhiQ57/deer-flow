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


def _active_data_query_state(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """读取父智能体当前 approved DataAgent 状态。

    Args:
        state: 父智能体状态。

    Returns:
        最新 approved DataAgent 状态；不存在时返回 None。
    """
    values = state.get("service_states")
    if not isinstance(values, list):
        return None
    return next(
        (dict(item) for item in reversed(values) if isinstance(item, Mapping) and item.get("service_name") == "data_query" and item.get("stage") == "approved"),
        None,
    )


def _gateway_failure(
    *,
    snapshot_id: str,
    data_source_id: str,
    error_code: str,
) -> dict[str, Any]:
    """构造 Gateway 调用失败的安全工具结果。

    Args:
        snapshot_id: 当前 Snapshot。
        data_source_id: 当前数据源标识。
        error_code: 安全错误码。

    Returns:
        可写入 ToolMessage artifact 的失败合同。
    """
    return {
        "version": 1,
        "kind": "data_query_sql_result",
        "snapshot_id": snapshot_id,
        "data_source_id": data_source_id,
        "validation": {
            "version": 1,
            "valid": False,
            "error_code": error_code,
            "snapshot_id": snapshot_id,
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
        snapshot_id: str,
        sql: str,
        data_source_id: str,
        validation_digest: str | None = None,
    ) -> dict[str, Any]:
        """调用 Gateway 内部 SQL API。

        Args:
            path: 内部 API 路径。
            context: 子代理可信运行上下文。
            snapshot_id: 当前 Snapshot。
            sql: SQL SubAgent 生成的候选 SQL。
            data_source_id: 当前数据源标识。
            validation_digest: 可选的 Gateway SQL 校验摘要。

        Returns:
            Gateway 返回的权威 SQL 合同；请求失败时返回安全失败合同。
        """
        if not all((context.thread_id, context.run_id, context.user_id, context.agent_name)):
            return _gateway_failure(
                snapshot_id=snapshot_id,
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
                    "snapshot_id": snapshot_id,
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
            snapshot_id=snapshot_id,
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
        active = _active_data_query_state(context.parent_state)
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
            or active is None
            or not all((context.thread_id, context.run_id, context.user_id, context.agent_name))
        ):
            return []
        payload = active.get("payload")
        approval = payload.get("approval") if isinstance(payload, Mapping) else None
        action = approval.get("action") if isinstance(approval, Mapping) and approval.get("status") == "approved" else None
        snapshot_id = active.get("snapshot_id")
        if action not in {"execute", "sql_only"} or not isinstance(snapshot_id, str) or not snapshot_id:
            return []
        if not sql_execution_runtime_registry.authorize_snapshot(
            run_id=context.run_id,
            thread_id=context.thread_id,
            user_id=context.user_id,
            agent_name=context.agent_name,
            snapshot_id=snapshot_id,
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
                snapshot_id=snapshot_id,
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
                snapshot_id=snapshot_id,
                sql=sql,
                data_source_id=str(ability["data_source_id"]),
                validation_digest=validation_digest,
            )
            return json.dumps(result, ensure_ascii=False, sort_keys=True), result

        tools: list[BaseTool] = [
            StructuredTool.from_function(
                coroutine=validate,
                name="data_validate_sql",
                description="通过 Gateway 按当前 approved Snapshot 校验单条只读 SQL。",
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
