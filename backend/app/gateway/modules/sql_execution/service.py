"""Gateway DataAgent PostgreSQL/MySQL 只读 SQL 执行服务。"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping
from typing import Any

from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig

from . import drivers
from .binding import resolve_data_source_binding, resolve_execution_dsn
from .contracts import (
    SqlExecutionRequest,
    SqlExecutionResult,
    SqlValidationRequest,
    SqlValidationResult,
)
from .error_classifier import classify_execution_error
from .result_budget import bound_result
from .validator import sql_digest, validate_sql_request


class SqlExecutionService:
    """Gateway SQL 校验与只读执行服务。

    手动 UI 路由和 SQL SubAgent 内部路由共用此服务；Harness 不持有数据库
    驱动、DSN、Secret 或 SQL 执行实现。
    """

    def __init__(
        self,
        config: DataQueryServiceAbilityConfig,
        *,
        env: Mapping[str, str] | None = None,
        secrets: Mapping[str, str] | None = None,
    ) -> None:
        """初始化 Gateway SQL Execution Service。

        Args:
            config: 当前 DataAgent 查询能力配置。
            env: 进程环境变量映射；测试可注入隔离环境。
            secrets: 当前请求上下文中的短期 Secret。
        """
        self._config = config
        self._env = env if env is not None else os.environ
        self._secrets = secrets

    @property
    def config(self) -> DataQueryServiceAbilityConfig:
        """返回当前只读配置。

        Returns:
            当前 DataAgent 查询能力配置。
        """
        return self._config

    def validate(self, request: SqlValidationRequest) -> SqlValidationResult:
        """校验 SQL 并生成绑定当前来源和 Snapshot 的规范 SQL。

        Args:
            request: SQL 校验请求。

        Returns:
            版本化 SQL 校验结果。
        """
        manual_binding: Mapping[str, Any] | None = None
        if request.source == "manual_ui":
            try:
                manual_binding = resolve_data_source_binding(
                    self._config,
                    env=self._env,
                    secrets=self._secrets,
                )
            except ValueError:
                return {
                    "version": 1,
                    "valid": False,
                    "error_code": "SQL_BINDING_MISMATCH",
                }
        return validate_sql_request(
            request,
            config=self._config,
            manual_binding=manual_binding,
        )

    def execute(self, request: SqlExecutionRequest) -> SqlExecutionResult:
        """执行最近一次已校验 SQL。

        Args:
            request: SQL 执行请求。

        Returns:
            JSON 安全的原始数据库结果或结构化错误。
        """
        started_at = time.perf_counter()
        validation = request.validation
        database_type = self._config.sql_execution.database_type
        result_identity = {
            "database_type": database_type,
            "sql_sha256": validation.get("sql_sha256"),
            "validation_digest": validation.get("validation_digest"),
        }
        if validation.get("valid") is not True or validation.get("sql_sha256") != sql_digest(request.sql) or validation.get("validation_digest") != request.validation_digest or validation.get("source") != request.source:
            return {
                "version": 1,
                "ok": False,
                "error_code": "SQL_DIGEST_MISMATCH",
                "error_category": "validation_error",
                "retryable": True,
                "recommended_action": "revalidate_sql",
                "duration_ms": 0,
                **result_identity,
            }
        try:
            dsn = resolve_execution_dsn(
                self._config,
                self._env,
                self._secrets,
            )
        except ValueError:
            return {
                "version": 1,
                "ok": False,
                "error_code": "SQL_DSN_MISSING",
                "error_category": "configuration_error",
                "retryable": False,
                "recommended_action": "stop",
                "duration_ms": 0,
                **result_identity,
            }
        try:
            current_binding = resolve_data_source_binding(
                self._config,
                env=self._env,
                secrets=self._secrets,
            )
        except ValueError:
            return {
                "version": 1,
                "ok": False,
                "error_code": "SQL_BINDING_MISMATCH",
                "error_category": "binding_error",
                "retryable": False,
                "recommended_action": "stop",
                "duration_ms": 0,
                **result_identity,
            }
        if validation.get("database_type") != database_type or validation.get("binding_fingerprint") != current_binding.get("binding_fingerprint"):
            return {
                "version": 1,
                "ok": False,
                "error_code": "SQL_BINDING_MISMATCH",
                "error_category": "binding_error",
                "retryable": False,
                "recommended_action": "stop",
                "duration_ms": 0,
                **result_identity,
            }
        try:
            if database_type == "mysql":
                columns, rows = drivers.execute_mysql(
                    request.sql,
                    dsn,
                    self._config,
                )
            else:
                columns, rows = drivers.execute_postgres(
                    request.sql,
                    dsn,
                    self._config,
                )
            bounded = bound_result(
                rows,
                columns,
                config=self._config,
                validation=validation,
            )
            return {
                "version": 1,
                "ok": True,
                "duration_ms": round(
                    (time.perf_counter() - started_at) * 1000,
                    2,
                ),
                **bounded,
                **result_identity,
            }
        except Exception as exc:
            error = classify_execution_error(
                exc,
                database_type,
                dsn=dsn,
            )
            result: SqlExecutionResult = {
                "version": 1,
                "ok": False,
                "error_code": error.error_code,
                "error_category": error.category,
                "retryable": error.retryable,
                "recommended_action": error.recommended_action,
                "duration_ms": round(
                    (time.perf_counter() - started_at) * 1000,
                    2,
                ),
                **result_identity,
            }
            if error.message is not None:
                result["error_message"] = error.message
            return result

    async def aexecute(
        self,
        request: SqlExecutionRequest,
    ) -> SqlExecutionResult:
        """在线程池中执行同步数据库调用。

        Args:
            request: SQL 执行请求。

        Returns:
            JSON 安全的原始数据库结果或结构化错误。
        """
        return await asyncio.to_thread(self.execute, request)


def validate_sql(
    sql: str,
    *,
    config: DataQueryServiceAbilityConfig,
    retrieval: Mapping[str, Any],
    snapshot_id: str | None = None,
) -> SqlValidationResult:
    """使用 Gateway SQL Service 校验候选 SQL。

    Args:
        sql: SQL 模型生成的候选 SQL。
        config: 当前 DataAgent 查询能力配置。
        retrieval: 当前 Query Snapshot 的 TableRAG registry。
        snapshot_id: 当前已批准 Query Snapshot 标识。

    Returns:
        版本化 SQL 校验结果。
    """
    return SqlExecutionService(config).validate(
        SqlValidationRequest(
            sql=sql,
            retrieval=retrieval,
            snapshot_id=snapshot_id,
        ),
    )


def execute_sql(
    sql: str,
    *,
    validation_digest: str,
    validation: Mapping[str, Any],
    config: DataQueryServiceAbilityConfig,
    secrets: Mapping[str, str] | None = None,
) -> SqlExecutionResult:
    """使用 Gateway SQL Service 执行已校验 SQL。

    Args:
        sql: ``validate_sql`` 返回的规范 SQL。
        validation_digest: 同一次校验返回的服务端摘要。
        validation: 最近一次 SQL 校验结果。
        config: 当前 DataAgent 查询能力配置。
        secrets: 当前请求上下文中的短期数据库 Secret。

    Returns:
        JSON 安全的原始数据库结果或结构化错误。
    """
    return SqlExecutionService(config, secrets=secrets).execute(
        SqlExecutionRequest(
            sql=sql,
            validation_digest=validation_digest,
            validation=validation,
        ),
    )
