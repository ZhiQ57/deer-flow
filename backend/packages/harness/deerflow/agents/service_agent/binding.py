"""DataAgent TableRAG 与 SQL 执行数据源绑定。"""

# ADD: DataAgent 正式查询闭环新增，服务端解析连接目标并只保存无密钥 fingerprint。
from __future__ import annotations

import os
from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

from .config import DataQueryServiceAbilityConfig


def _target_fingerprint(dsn: str) -> str:
    """从 DSN 的 host/port/database 计算不含账号密码的目标 fingerprint。"""
    parsed = urlsplit(dsn)
    if not parsed.scheme or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("数据库 DSN 缺少 scheme、host 或 database。")
    scheme = parsed.scheme.lower().split("+", 1)[0]
    port = parsed.port or (3306 if scheme == "mysql" else 5432)
    target = f"{scheme}://{parsed.hostname.lower()}:{port}/{parsed.path.strip('/').lower()}"
    return f"sha256:{sha256(target.encode()).hexdigest()}"


def resolve_execution_dsn(
    config: DataQueryServiceAbilityConfig,
    env: Mapping[str, str],
    secrets: Mapping[str, str] | None = None,
) -> str:
    """解析 SQL 执行 DSN 环境变量或请求级 Secret。

    Args:
        config: 当前 DataAgent 能力配置。
        env: 进程环境变量映射。
        secrets: 请求上下文中未持久化的 Secret 名称和值。

    Returns:
        仅在当前调用栈使用的真实 DSN。

    Raises:
        ValueError: 引用缺失或值为空。
    """
    if config.sql_execution.dsn_env.startswith("secret://"):
        secret_name = config.sql_execution.dsn_env.removeprefix("secret://")
        dsn = secrets.get(secret_name) if isinstance(secrets, Mapping) else None
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("SQL 执行 DSN Secret 缺失。")
        return dsn.strip()
    dsn = env.get(config.sql_execution.dsn_env)
    if not isinstance(dsn, str) or not dsn.strip():
        raise ValueError("SQL 执行 DSN Secret 缺失。")
    return dsn.strip()


def _read_retrieval_dsn(env: Mapping[str, str]) -> str:
    """读取 TableRAG MCP 索引库 DSN。"""
    for name in ("TABLERAG_MCP_INDEX_DSN", "TABLERAG_INDEX_DSN"):
        value = env.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("TableRAG 索引库 DSN Secret 缺失。")


# ADD: 构造 DataSourceBindingV1；默认强制同物理目标，显式逻辑绑定允许索引库与业务库分离。
def resolve_data_source_binding(
    config: DataQueryServiceAbilityConfig,
    *,
    env: Mapping[str, str] | None = None,
    secrets: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """解析并校验 DataAgent 单数据源绑定。"""
    resolved_env = env or os.environ
    execution_fingerprint = _target_fingerprint(resolve_execution_dsn(config, resolved_env, secrets))
    retrieval_fingerprint = _target_fingerprint(_read_retrieval_dsn(resolved_env))
    if config.source_binding_mode == "same_physical_target" and execution_fingerprint != retrieval_fingerprint:
        raise ValueError("TableRAG 检索库与 SQL 执行库不是同一数据源。")
    binding_fingerprint = f"sha256:{sha256(f'{config.data_source_id}\n{config.source_binding_mode}\n{retrieval_fingerprint}\n{execution_fingerprint}'.encode()).hexdigest()}"
    return {
        "version": 1,
        "data_source_id": config.data_source_id,
        "database_type": config.sql_execution.database_type,
        "source_binding_mode": config.source_binding_mode,
        "table_rag_config_ref": config.table_rag_config,
        "retrieval_target_fingerprint": retrieval_fingerprint,
        "execution_secret_ref": config.sql_execution.dsn_env,
        "execution_target_fingerprint": execution_fingerprint,
        "binding_fingerprint": binding_fingerprint,
        "allowed_schemas": list(config.sql_execution.allowed_schemas),
    }
