"""DataAgent SQL SubAgent 工具装配。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from threading import Lock
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from .config import DataQueryServiceAbilityConfig
from .sql_executor import (
    SqlExecutionRequest,
    SqlExecutionService,
    SqlValidationRequest,
    sql_digest,
)


def _json_content(value: Mapping[str, Any]) -> str:
    """把 SQL 工具结果序列化为安全 JSON。

    Args:
        value: SQL 校验或执行结果。

    Returns:
        不包含 Python 异常对象的 JSON 文本。
    """
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, default=str)


def build_sql_tools(
    config: DataQueryServiceAbilityConfig,
    service_state: Mapping[str, Any],
    *,
    secrets: Mapping[str, str] | None = None,
    executor: SqlExecutionService | None = None,
) -> list[BaseTool]:
    """构造绑定当前 Query Snapshot 的 SQL SubAgent 工具。

    Args:
        config: 当前 DataAgent 查询能力配置。
        service_state: 当前已批准的 DataAgent 服务状态。
        secrets: 当前请求上下文中的短期数据库 Secret。
        executor: 可选共享 SQL Executor；默认按当前配置创建。

    Returns:
        ``data_validate_sql``，以及 execute 动作下的 ``data_execute_sql``。
    """
    payload = service_state.get("payload") if isinstance(service_state, Mapping) else None
    retrieval = payload.get("retrieval") if isinstance(payload, Mapping) else None
    retrieval = retrieval if isinstance(retrieval, Mapping) else {}
    approval = payload.get("approval") if isinstance(payload, Mapping) else None
    action = approval.get("action") if isinstance(approval, Mapping) and approval.get("status") == "approved" else None
    if action not in {"execute", "sql_only"}:
        return []

    sql_executor = executor or SqlExecutionService(config, secrets=secrets)
    validation_holder: dict[str, Any] = {}
    validation_generation = 0
    consumed_validation_generation = 0
    execution_attempts = 0
    execution_succeeded = False
    session_lock = Lock()
    snapshot_id = str(service_state.get("snapshot_id") or "")
    max_attempts = config.sql_execution.max_execution_attempts

    def validate(sql: str) -> str:
        """校验候选 SQL，并登记新的可执行校验轮次。

        Args:
            sql: SQL 模型生成或修复后的候选 SQL。

        Returns:
            版本化 SQL 校验 JSON。
        """
        nonlocal validation_generation
        result = sql_executor.validate(
            SqlValidationRequest(
                sql=sql,
                retrieval=retrieval,
                snapshot_id=snapshot_id,
            )
        )
        if result.get("valid") is True:
            with session_lock:
                validation_holder.clear()
                validation_holder.update(result)
                validation_generation += 1
        return _json_content(result)

    def execute(sql: str, validation_digest: str) -> str:
        """执行最近一次尚未消费的有效 SQL 校验结果。

        Args:
            sql: ``data_validate_sql`` 返回的 ``executable_sql``。
            validation_digest: 同一次校验返回的服务端摘要。

        Returns:
            版本化 SQL 执行 JSON。
        """
        nonlocal consumed_validation_generation, execution_attempts, execution_succeeded
        with session_lock:
            validation_matches = validation_holder.get("valid") is True and validation_holder.get("sql_sha256") == sql_digest(sql) and validation_holder.get("validation_digest") == validation_digest
            if not validation_matches:
                return _json_content(
                    {
                        "version": 1,
                        "ok": False,
                        "database_type": config.sql_execution.database_type,
                        "error_code": "SQL_DIGEST_MISMATCH",
                        "error_category": "validation_error",
                        "retryable": True,
                        "recommended_action": "revalidate_sql",
                        "snapshot_id": snapshot_id,
                        "validation_digest": validation_holder.get("validation_digest"),
                        "attempt": execution_attempts,
                        "max_attempts": max_attempts,
                    }
                )
            if execution_succeeded or execution_attempts >= max_attempts or consumed_validation_generation == validation_generation:
                if execution_succeeded:
                    error_category = "execution_complete"
                    retryable = False
                    recommended_action = "stop"
                elif execution_attempts >= max_attempts:
                    error_category = "attempt_budget"
                    retryable = False
                    recommended_action = "stop"
                else:
                    error_category = "validation_reuse"
                    retryable = True
                    recommended_action = "revalidate_sql"
                return _json_content(
                    {
                        "version": 1,
                        "ok": False,
                        "database_type": config.sql_execution.database_type,
                        "error_code": "SQL_EXECUTION_ALREADY_ATTEMPTED",
                        "error_category": error_category,
                        "retryable": retryable,
                        "recommended_action": recommended_action,
                        "snapshot_id": snapshot_id,
                        "validation_digest": validation_holder.get("validation_digest"),
                        "attempt": execution_attempts,
                        "max_attempts": max_attempts,
                    }
                )
            execution_attempts += 1
            attempt = execution_attempts
            consumed_validation_generation = validation_generation
            validation = dict(validation_holder)

        result = sql_executor.execute(
            SqlExecutionRequest(
                sql=sql,
                validation_digest=validation_digest,
                validation=validation,
            )
        )
        result["snapshot_id"] = snapshot_id
        result["attempt"] = attempt
        result["max_attempts"] = max_attempts
        if result.get("ok") is True:
            with session_lock:
                execution_succeeded = True
        return _json_content(result)

    tools: list[BaseTool] = [
        StructuredTool.from_function(
            func=validate,
            name="data_validate_sql",
            description="按当前 DataAgent Evidence、数据库方言和 allowlist 校验单条只读 SQL；SQL 修复后必须重新调用。",
        )
    ]
    if action == "execute":
        tools.append(
            StructuredTool.from_function(
                func=execute,
                name="data_execute_sql",
                description="执行最近一次尚未消费的 data_validate_sql 结果；执行失败后必须修复或确认 SQL，并重新校验后才能再次执行。",
            )
        )
    return tools
