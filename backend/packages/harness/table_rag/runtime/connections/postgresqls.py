"""PostgreSQL 运行时连接和索引能力校验。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...configs import TableRAGConfig
from .base import ConnectionProvider
from .validators import ConnectionValidationResult, ConnectionValidator
from ..indexing.index_tables.postgres.requirements import (
    INDEX_METADATA_TABLE_NAME,
    PostgresIndexTableRequirement,
    build_postgres_runtime_requirements,
    index_metadata_table_requirement,
)
from ..indexing.index_tables.postgres.ddl import qualified_table_name


class PostgresConnectionValidator(ConnectionValidator):
    """PostgreSQL 连接校验器，不创建连接池，只检查外部连接能力。"""

    def __init__(self, connection_provider: ConnectionProvider):
        """初始化 PostgreSQL 连接校验器。

        Args:
            connection_provider: 外部注入的数据库连接提供器。

        Returns:
            None。
        """
        self.connection_provider = connection_provider

    def validate(self, config: TableRAGConfig) -> ConnectionValidationResult:
        """校验 PostgreSQL 连接、版本、扩展、权限和索引表结构。

        Args:
            config: TableRAG 总配置。

        Returns:
            连接校验结果。
        """
        result = ConnectionValidationResult(database_type="postgresql")
        requirements = build_postgres_runtime_requirements(config)
        try:
            with self.connection_provider.connect() as conn:
                self._check_connectivity(conn, result)
                self._check_version(conn, requirements.min_version_num, result)
                self._check_extensions(conn, requirements.required_extensions, result)
                self._check_schema_permissions(conn, result)
                table_status = self._check_index_tables(conn, requirements.required_tables, result)
                self._check_indexes(conn, requirements.required_indexes, result)
                metadata_table = self._check_index_metadata_table(conn, config, result)
                if metadata_table.get("exists") and not metadata_table.get("missing_columns"):
                    self._check_schema_versions(
                        conn,
                        config.index_store.schema_name,
                        requirements.required_schema_versions,
                        result,
                    )
                self._check_vector_dimensions(conn, config, table_status, result)
        except Exception as exc:
            result.add_issue(
                "postgres_connection_failed",
                f"PostgreSQL 连接校验失败：{exc}",
                hint="请确认外部业务项目传入的连接提供器可用，且数据库网络、账号和权限正确。",
                metadata={"exception_type": type(exc).__name__},
            )
        return result

    def _check_connectivity(self, conn: Any, result: ConnectionValidationResult) -> None:
        """检查连接是否可以执行最小查询。

        Args:
            conn: 外部提供的数据库连接对象。
            result: 校验结果对象。

        Returns:
            None。
        """
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
        if _first_value(row) != 1:
            result.add_issue("postgres_ping_failed", "PostgreSQL SELECT 1 未返回预期结果。")

    def _check_version(self, conn: Any, min_version_num: int, result: ConnectionValidationResult) -> None:
        """检查 PostgreSQL 版本是否满足最低要求。

        Args:
            conn: 外部提供的数据库连接对象。
            min_version_num: 最低 server_version_num。
            result: 校验结果对象。

        Returns:
            None。
        """
        with conn.cursor() as cur:
            cur.execute("SHOW server_version_num")
            raw_version = _first_value(cur.fetchone())
        version_num = int(raw_version)
        result.metadata["server_version_num"] = version_num
        if version_num < min_version_num:
            result.add_issue(
                "postgres_version_too_old",
                f"PostgreSQL 版本过低：{version_num}，最低要求为 {min_version_num}。",
                hint="请升级 PostgreSQL 或使用满足 TableRAG 索引能力要求的数据库实例。",
                metadata={"server_version_num": version_num, "min_version_num": min_version_num},
            )

    def _check_extensions(
        self,
        conn: Any,
        required_extensions: Iterable[str],
        result: ConnectionValidationResult,
    ) -> None:
        """检查必需 PostgreSQL 扩展是否已安装。

        Args:
            conn: 外部提供的数据库连接对象。
            required_extensions: 配置启用能力对应的扩展集合。
            result: 校验结果对象。

        Returns:
            None。
        """
        required = set(required_extensions)
        if not required:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname = ANY(%s)", (sorted(required),))
            rows = cur.fetchall()
        installed = {_first_value(row) for row in rows}
        missing = sorted(required - installed)
        result.metadata["installed_extensions"] = sorted(installed)
        if missing:
            result.add_issue(
                "postgres_missing_extension",
                f"PostgreSQL 缺少必需扩展：{', '.join(missing)}。",
                hint="请由数据库管理员安装扩展，或在初始化索引前授予 CREATE EXTENSION 所需权限。",
                metadata={"missing_extensions": missing},
            )

    def _check_schema_permissions(self, conn: Any, result: ConnectionValidationResult) -> None:
        """检查当前连接是否具备创建索引结构所需基础权限。

        Args:
            conn: 外部提供的数据库连接对象。
            result: 校验结果对象。

        Returns:
            None。
        """
        with conn.cursor() as cur:
            cur.execute("SELECT has_database_privilege(current_database(), 'CREATE')")
            can_create = bool(_first_value(cur.fetchone()))
        result.metadata["can_create_in_database"] = can_create
        if not can_create:
            result.add_issue(
                "postgres_missing_create_privilege",
                "当前 PostgreSQL 用户缺少在数据库中创建 schema / extension 的权限。",
                hint="请在业务项目或数据库侧授予 CREATE 权限，或由管理员显式执行索引初始化脚本。",
            )

    def _check_index_tables(
        self,
        conn: Any,
        table_requirements: list[PostgresIndexTableRequirement],
        result: ConnectionValidationResult,
    ) -> dict[str, dict[str, Any]]:
        """检查索引表是否存在且字段结构匹配。

        Args:
            conn: 外部提供的数据库连接对象。
            table_requirements: 索引表结构要求列表。
            result: 校验结果对象。

        Returns:
            表结构检查状态映射。
        """
        table_status: dict[str, dict[str, Any]] = {}
        with conn.cursor() as cur:
            for requirement in table_requirements:
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (requirement.schema_name, requirement.table_name),
                )
                columns = {_first_value(row) for row in cur.fetchall()}
                key = f"{requirement.schema_name}.{requirement.table_name}"
                missing_columns = sorted(requirement.required_columns - columns)
                table_status[key] = {
                    "exists": bool(columns),
                    "missing_columns": missing_columns,
                }
                if not columns:
                    result.add_issue(
                        "postgres_missing_index_table",
                        f"PostgreSQL 索引表不存在：{key}。",
                        hint="请通过 runtime.indexing.index_tables.postgres.create_postgres_indexes 或脚本显式初始化索引结构。",
                        metadata={"table": key},
                    )
                elif missing_columns:
                    result.add_issue(
                        "postgres_index_table_columns_mismatch",
                        f"PostgreSQL 索引表字段不匹配：{key} 缺少 {', '.join(missing_columns)}。",
                        hint="请重新执行幂等索引初始化，或检查数据库中历史表结构。",
                        metadata={"table": key, "missing_columns": missing_columns},
                    )
        result.metadata["index_tables"] = table_status
        return table_status

    def _check_indexes(
        self,
        conn: Any,
        required_indexes: dict[str, set[str]],
        result: ConnectionValidationResult,
    ) -> None:
        """检查必需 PostgreSQL 索引是否存在。

        Args:
            conn: 外部提供的数据库连接对象。
            required_indexes: schema 到必需索引名集合的映射。
            result: 校验结果对象。

        Returns:
            None。
        """
        if not required_indexes:
            return
        index_status: dict[str, dict[str, list[str]]] = {}
        all_missing: list[str] = []
        with conn.cursor() as cur:
            for schema_name, index_names in sorted(required_indexes.items()):
                cur.execute(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = %s AND indexname = ANY(%s)
                    """,
                    (schema_name, sorted(index_names)),
                )
                rows = cur.fetchall()
                existing = {_first_value(row) for row in rows}
                missing = sorted(index_names - existing)
                key = schema_name
                index_status[key] = {
                    "required": sorted(index_names),
                    "existing": sorted(existing),
                    "missing": missing,
                }
                all_missing.extend(f"{schema_name}.{index_name}" for index_name in missing)
        result.metadata["indexes"] = index_status
        if all_missing:
            result.add_issue(
                "postgres_missing_index",
                f"PostgreSQL 缺少必需索引：{', '.join(all_missing)}。",
                hint="请重新执行 PostgreSQL 索引初始化，确保 pg_search BM25 索引和 pg_trgm 模糊匹配索引已创建。",
                metadata={"missing_indexes": all_missing},
            )

    def _check_index_metadata_table(
        self,
        conn: Any,
        config: TableRAGConfig,
        result: ConnectionValidationResult,
    ) -> dict[str, Any]:
        """检查 SDK 托管索引元数据表，缺失时不阻断在线检索。

        Args:
            conn: 外部提供的数据库连接对象。
            config: TableRAG 总配置。
            result: 校验结果对象。

        Returns:
            元数据表结构检查状态。
        """
        requirement = index_metadata_table_requirement(config.index_store)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                """,
                (requirement.schema_name, requirement.table_name),
            )
            columns = {_first_value(row) for row in cur.fetchall()}
        key = f"{requirement.schema_name}.{requirement.table_name}"
        missing_columns = sorted(requirement.required_columns - columns)
        metadata_table = {
            "exists": bool(columns),
            "missing_columns": missing_columns,
        }
        result.metadata["index_metadata_table"] = {key: metadata_table}
        if not columns:
            result.add_issue(
                "postgres_missing_index_metadata",
                f"PostgreSQL 索引元数据表不存在：{key}。",
                severity="warning",
                hint=(
                    "该表只用于 SDK 托管初始化和 schema version 校验；"
                    "如果你手工维护四张索引表，可忽略该警告。"
                ),
                metadata={"table": key},
            )
        elif missing_columns:
            result.add_issue(
                "postgres_index_metadata_columns_mismatch",
                f"PostgreSQL 索引元数据表字段不匹配：{key} 缺少 {', '.join(missing_columns)}。",
                severity="warning",
                hint=(
                    "该表只用于 SDK 托管初始化和 schema version 校验；"
                    "字段不完整时将跳过版本校验。"
                ),
                metadata={"table": key, "missing_columns": missing_columns},
            )
        return metadata_table

    def _check_schema_versions(
        self,
        conn: Any,
        schema_name: str,
        required_versions: dict[str, int],
        result: ConnectionValidationResult,
    ) -> None:
        """检查索引组件 schema version 是否满足要求。

        Args:
            conn: 外部提供的数据库连接对象。
            schema_name: 索引元数据表所在 schema。
            required_versions: 组件最低版本要求。
            result: 校验结果对象。

        Returns:
            None。
        """
        if not required_versions:
            return
        table = qualified_table_name(schema_name, INDEX_METADATA_TABLE_NAME)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT component, schema_version
                FROM {table}
                WHERE component = ANY(%s)
                """,
                (sorted(required_versions),),
            )
            rows = cur.fetchall()
        actual_versions = {row[0]: int(row[1]) for row in rows}
        result.metadata["schema_versions"] = actual_versions
        for component, required_version in sorted(required_versions.items()):
            actual_version = actual_versions.get(component)
            if actual_version is None:
                result.add_issue(
                    "postgres_missing_index_schema_version",
                    f"PostgreSQL 索引组件缺少 schema version：{component}。",
                    hint="请重新执行幂等索引初始化，让 agent_index_metadata 写入组件版本。",
                    metadata={"component": component, "required_version": required_version},
                )
            elif actual_version < required_version:
                result.add_issue(
                    "postgres_index_schema_version_mismatch",
                    f"PostgreSQL 索引组件版本过低：{component}={actual_version}，要求 {required_version}。",
                    hint="请重新执行索引初始化或迁移脚本，确保历史索引表结构升级到当前 SDK 版本。",
                    metadata={
                        "component": component,
                        "actual_version": actual_version,
                        "required_version": required_version,
                    },
                )

    def _check_vector_dimensions(
        self,
        conn: Any,
        config: TableRAGConfig,
        table_status: dict[str, dict[str, Any]],
        result: ConnectionValidationResult,
    ) -> None:
        """检查 pgvector 字段维度是否与配置一致。

        Args:
            conn: 外部提供的数据库连接对象。
            config: TableRAG 总配置。
            table_status: 索引表结构检查结果。
            result: 校验结果对象。

        Returns:
            None。
        """
        if not config.index_store.requires_pgvector:
            return
        checks: list[tuple[str, str, str, int]] = []
        if config.index_store.table_embedding_enabled:
            checks.append(
                (
                    config.index_store.schema_name,
                    config.index_store.table_index_name,
                    "embedding",
                    config.index_store.table_embedding_dimension,
                )
            )
        if config.index_store.column_embedding_enabled:
            checks.append(
                (
                    config.index_store.schema_name,
                    config.index_store.column_index_name,
                    "embedding",
                    config.index_store.column_embedding_dimension,
                )
            )
        if config.index_store.evidence_embedding_enabled:
            checks.append(
                (
                    config.index_store.schema_name,
                    config.index_store.evidence_embedding_table_name,
                    "embedding",
                    config.index_store.evidence_embedding_dimension,
                )
            )
        if config.index_store.field_value_embedding_enabled:
            checks.append(
                (
                    config.index_store.schema_name,
                    config.index_store.field_value_embedding_table_name,
                    "embedding",
                    config.index_store.field_value_embedding_dimension,
                )
            )
        actual_types: dict[str, str | None] = {}
        expected_types: dict[str, str] = {}
        with conn.cursor() as cur:
            for schema_name, table_name, column_name, expected_dimension in checks:
                key = f"{schema_name}.{table_name}"
                status = table_status.get(key, {})
                if not status.get("exists") or column_name in status.get("missing_columns", []):
                    continue
                cur.execute(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod)
                    FROM pg_attribute a
                    WHERE a.attrelid = %s::regclass
                      AND a.attname = %s
                      AND NOT a.attisdropped
                    """,
                    (key, column_name),
                )
                type_name = _first_value(cur.fetchone())
                column_key = f"{key}.{column_name}"
                actual_types[column_key] = str(type_name) if type_name is not None else None
                expected_types[column_key] = f"vector({expected_dimension})"
        result.metadata["vector_column_types"] = actual_types
        for column_key, actual_type in sorted(actual_types.items()):
            expected_type = expected_types[column_key]
            if actual_type != expected_type:
                result.add_issue(
                    "postgres_vector_dimension_mismatch",
                    f"PostgreSQL 向量字段维度不匹配：{column_key}={actual_type}，要求 {expected_type}。",
                    hint="请重新执行 runtime.indexing.index_tables.postgres.create_postgres_indexes，或手工迁移历史向量列到正确维度。",
                    metadata={
                        "column": column_key,
                        "actual_type": actual_type,
                        "expected_type": expected_type,
                    },
                )


def validate_postgres_connection(
    connection_provider: ConnectionProvider,
    config: TableRAGConfig,
) -> ConnectionValidationResult:
    """校验外部注入的 PostgreSQL 连接是否满足 TableRAG 要求。

    Args:
        connection_provider: 外部注入的数据库连接提供器。
        config: TableRAG 总配置。

    Returns:
        连接校验结果。
    """
    return PostgresConnectionValidator(connection_provider).validate(config)


def _first_value(row: Any) -> Any:
    """从 DB-API 行对象中读取第一列。

    Args:
        row: fetchone 或 fetchall 返回的单行数据。

    Returns:
        第一列取值。
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    if isinstance(row, (tuple, list)):
        return row[0] if row else None
    return row
