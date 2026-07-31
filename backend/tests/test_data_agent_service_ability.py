"""DataAgent service ability 配置、注册和状态 reducer 测试。"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from deerflow.agents.middlewares.query_labels_middleware import QueryLabelsMiddleware
from deerflow.agents.service_agent.query_intent_approval_middleware import QueryIntentApprovalMiddleware
from app.gateway.modules.sql_execution.binding import resolve_data_source_binding
from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig, parse_service_ability
from deerflow.agents.service_agent.registry import DataAgentServiceAbility, resolve_service_ability, resolve_service_ability_safely
from deerflow.agents.service_agent.sql_stage_middleware import SqlStageMiddleware
from deerflow.agents.service_agent.table_rag_middleware import TableRagStageMiddleware
from deerflow.agents.thread_state import merge_service_states
from deerflow.config.agents_config import AgentConfig, preserve_non_managed_fields
from deerflow.tools.tools import BUILTIN_TOOLS


def _ability_config(**overrides: object) -> dict[str, object]:
    """构造首版 DataAgent service ability 测试配置。"""
    config: dict[str, object] = {
        "type": "data_query",
        "version": 1,
        "enable_sql_rag": True,
        "table_rag_config": "tabelrag.yaml",
        "data_source_id": "text2sql-pg-local",
        "confirmation_mode": "on_ambiguity",
        "min_auto_confidence": 0.85,
        "sql_subagent_name": "sql-subagent",
        "sql_execution": {
            "enabled": True,
            "database_type": "postgresql",
            "dsn_env": "DATA_AGENT_SQL_DSN",
            "readonly": True,
            "statement_timeout_seconds": 30,
            "max_rows": 500,
            "max_result_chars": 100000,
            "allowed_schemas": ["public"],
        },
    }
    config.update(overrides)
    return config


def test_agent_config_preserves_service_ability() -> None:
    """配置写回必须完整保留 service_ability。"""
    raw = _ability_config(custom_future_field={"enabled": True})
    agent = AgentConfig(name="data-agent", service_ability=raw)

    preserved = preserve_non_managed_fields(agent)

    assert preserved["service_ability"] == raw


def test_data_agent_service_ability_registers_data_query_middlewares() -> None:
    """DataAgent 业务 middleware 不再按轮次强制重置，按检索、标签、意图审批和 SQL 阶段串行。"""
    config = parse_service_ability(_ability_config())
    assert config is not None

    middlewares = DataAgentServiceAbility(config).build_middlewares()

    assert [type(middleware) for middleware in middlewares] == [
        TableRagStageMiddleware,
        QueryLabelsMiddleware,
        QueryIntentApprovalMiddleware,
        SqlStageMiddleware,
    ]


def test_parse_service_ability_normalizes_postgres_alias() -> None:
    """PostgreSQL 方言别名归一化为唯一合同值。"""
    raw = _ability_config()
    raw["sql_execution"] = {**raw["sql_execution"], "database_type": "pg"}  # type: ignore[arg-type]

    parsed = parse_service_ability(raw)

    assert isinstance(parsed, DataQueryServiceAbilityConfig)
    assert parsed.sql_execution.database_type == "postgresql"
    assert parsed.model_extra == {}


def test_parse_service_ability_rejects_removed_static_table_column_allowlists() -> None:
    """已删除的静态表/字段配置不能继续进入 SQL Execution 合同。"""
    raw = _ability_config()
    raw["sql_execution"] = {
        **raw["sql_execution"],  # type: ignore[arg-type]
        "allowed_tables": ["orders"],
        "allowed_columns": ["orders.region"],
    }

    with pytest.raises(ValidationError):
        parse_service_ability(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "unknown"),
        ("version", 2),
        ("min_auto_confidence", 1.1),
    ],
)
def test_parse_service_ability_rejects_unsupported_contract(field: str, value: object) -> None:
    """能力类型、版本和阈值非法时必须 fail closed。"""
    with pytest.raises(ValidationError):
        parse_service_ability(_ability_config(**{field: value}))


def test_parse_service_ability_requires_readonly_database_execution() -> None:
    """PostgreSQL/MySQL SQL 能力都必须使用只读执行。"""
    raw = _ability_config()
    raw["sql_execution"] = {**raw["sql_execution"], "readonly": False}  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        parse_service_ability(raw)


def test_data_query_v1_rejects_unsupported_label_only_toggle() -> None:
    """v1 是完整 TableRAG→SQL 合同，关闭 SQL 应移除 service_ability，不能保留半装配状态。"""
    with pytest.raises(ValidationError):
        parse_service_ability(_ability_config(enable_sql_rag=False))


@pytest.mark.parametrize(
    "field",
    ["statement_timeout_seconds", "max_execution_attempts", "max_rows", "max_cell_chars", "max_result_chars"],
)
def test_parse_service_ability_rejects_boolean_sql_budgets(field: str) -> None:
    """Python bool 是 int 子类，但 SQL 数值预算绝不能把 true/false 当作 1/0。"""
    raw = _ability_config()
    raw["sql_execution"] = {**raw["sql_execution"], field: True}  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        parse_service_ability(raw)


def test_parse_service_ability_rejects_boolean_confidence() -> None:
    """自动批准阈值必须是真实数字，不能接受布尔值。"""
    with pytest.raises(ValidationError):
        parse_service_ability(_ability_config(min_auto_confidence=True))


def test_registry_resolves_only_explicit_data_query_type() -> None:
    """注册表只按显式 type/version 解析，不能依赖 agent 名称。"""
    resolved = resolve_service_ability(_ability_config())

    assert isinstance(resolved, DataAgentServiceAbility)
    assert resolved.config.type == "data_query"
    assert resolve_service_ability(None) is None
    assert resolve_service_ability_safely({"type": "unknown", "version": 1}) is None


def test_invalid_service_ability_logs_readable_path_without_secret(caplog: pytest.LogCaptureFixture) -> None:
    """配置失败日志必须指出字段路径，但不能回显用户误填的 DSN 或密码。"""
    raw = _ability_config()
    raw["sql_execution"] = {
        **raw["sql_execution"],  # type: ignore[arg-type]
        "dsn_env": "postgresql://readonly:super-secret@db.local/text2sql",
    }

    with caplog.at_level(logging.ERROR):
        resolved = resolve_service_ability_safely(raw)

    assert resolved is None
    assert "sql_execution.dsn_env" in caplog.text
    assert "修复建议" in caplog.text
    assert "super-secret" not in caplog.text
    assert "postgresql://" not in caplog.text


def test_data_agent_tools_are_scoped_to_service_ability() -> None:
    """DataAgent 专属工具不得暴露给默认 Agent，但适配器必须提供标签与意图审批工具。"""
    resolved = resolve_service_ability(_ability_config())
    assert isinstance(resolved, DataAgentServiceAbility)

    assert "publish_query_labels" not in {tool.name for tool in BUILTIN_TOOLS}
    assert "ask_intent_approval" not in {tool.name for tool in BUILTIN_TOOLS}
    assert [tool.name for tool in resolved.build_tools()] == ["publish_query_labels", "ask_intent_approval"]


def test_public_service_ability_metadata_is_sanitized() -> None:
    """Agents API 使用的能力投影不能包含 Secret 引用。"""
    resolved = resolve_service_ability(_ability_config())
    assert isinstance(resolved, DataAgentServiceAbility)

    metadata = resolved.public_metadata()

    assert metadata["type"] == "data_query"
    assert "dsn_env" not in metadata
    assert "sql_execution" not in metadata


def test_data_source_binding_compares_targets_without_exposing_dsn() -> None:
    """检索与执行目标必须同源，注入 Harness 的 binding 只能保留无密钥信息。"""
    config = parse_service_ability(_ability_config())
    assert config is not None
    binding = resolve_data_source_binding(
        config,
        env={
            "DATA_AGENT_SQL_DSN": "postgresql://readonly:secret@db.local:5432/text2sql",
            "TABLERAG_MCP_INDEX_DSN": "postgresql://indexer:other@db.local:5432/text2sql",
        },
    )

    assert binding["execution_target_fingerprint"] == binding["retrieval_target_fingerprint"]
    assert "execution_secret_ref" not in binding
    assert "dsn_env" not in binding
    assert "readonly:" not in str(binding)


def test_logical_data_source_binding_allows_postgres_index_and_mysql_execution() -> None:
    """真实部署可用 PostgreSQL 索引检索 MySQL 业务源，但必须显式声明逻辑绑定。"""
    raw = _ability_config(source_binding_mode="logical_data_source")
    raw["sql_execution"] = {
        **raw["sql_execution"],  # type: ignore[arg-type]
        "database_type": "mysql",
        "dsn_env": "DATA_AGENT_MYSQL_DSN",
    }
    config = parse_service_ability(raw)
    assert config is not None

    binding = resolve_data_source_binding(
        config,
        env={
            "DATA_AGENT_MYSQL_DSN": "mysql+pymysql://readonly:secret@db.local:3308/text2sql",
            "TABLERAG_MCP_INDEX_DSN": "postgresql://indexer:other@index.local:55433/text2sql",
        },
    )

    assert binding["source_binding_mode"] == "logical_data_source"
    assert binding["retrieval_target_fingerprint"] != binding["execution_target_fingerprint"]
    assert binding["binding_fingerprint"].startswith("sha256:")


def test_data_source_binding_resolves_request_scoped_secret_reference() -> None:
    """请求级 Secret 只在 Gateway 解析，注入 Harness 的 binding 不得保留引用和值。"""
    raw = _ability_config(source_binding_mode="logical_data_source")
    raw["sql_execution"] = {
        **raw["sql_execution"],  # type: ignore[arg-type]
        "database_type": "mysql",
        "dsn_env": "secret://data-agent-mysql",
    }
    config = parse_service_ability(raw)
    assert config is not None

    binding = resolve_data_source_binding(
        config,
        env={"TABLERAG_MCP_INDEX_DSN": "postgresql://indexer:other@index.local:55433/text2sql"},
        secrets={"data-agent-mysql": "mysql+pymysql://readonly:super-secret@db.local:3308/text2sql"},
    )

    assert binding["database_type"] == "mysql"
    assert "execution_secret_ref" not in binding
    assert "secret://data-agent-mysql" not in str(binding)
    assert "super-secret" not in str(binding)


def test_service_state_reducer_replaces_active_snapshot_by_service_name() -> None:
    """每个 service_name 只能保留一个活动快照。"""
    existing = [
        {
            "service_name": "data_query",
            "version": 1,
            "snapshot_id": "snapshot-old",
            "stage": "labels_published",
            "payload": {"labels": ["old"]},
            "updated_at": "2026-07-16T00:00:00Z",
        },
        {
            "service_name": "another_service",
            "version": 1,
            "snapshot_id": "other",
            "stage": "ready",
            "payload": {},
            "updated_at": "2026-07-16T00:00:00Z",
        },
    ]
    replacement = [
        {
            "service_name": "data_query",
            "version": 1,
            "snapshot_id": "snapshot-new",
            "stage": "awaiting_confirmation",
            "payload": {"labels": ["new"]},
            "updated_at": "2026-07-16T00:01:00Z",
        }
    ]

    merged = merge_service_states(existing, replacement)

    assert [item["service_name"] for item in merged] == ["another_service", "data_query"]
    assert merged[-1]["snapshot_id"] == "snapshot-new"
    assert merge_service_states(merged, replacement) == merged


def test_service_state_reducer_rejects_stage_rollback_and_stale_snapshot() -> None:
    """同一快照不能回退阶段，新快照建立后也不能被旧 SQL 结果覆盖。"""
    current = [
        {
            "service_name": "data_query",
            "version": 1,
            "turn_id": "turn-1",
            "snapshot_id": "snapshot-new",
            "stage": "approved",
            "payload": {"approval": {"status": "approved", "action": "execute"}},
            "updated_at": "2026-07-16T00:02:00Z",
        }
    ]
    rollback = [
        {
            **current[0],
            "stage": "awaiting_confirmation",
            "updated_at": "2026-07-16T00:03:00Z",
        }
    ]
    stale_result = [
        {
            **current[0],
            "snapshot_id": "snapshot-old",
            "stage": "succeeded",
            "updated_at": "2026-07-16T00:04:00Z",
        }
    ]

    assert merge_service_states(current, rollback) == current
    assert merge_service_states(current, stale_result) == current


def test_service_state_reducer_allows_revision_and_rejects_old_turn_result() -> None:
    """确认修改可创建同轮新检索快照，新用户轮次建立后拒绝旧轮结果。"""
    awaiting = [
        {
            "service_name": "data_query",
            "version": 1,
            "turn_id": "turn-1",
            "snapshot_id": "snapshot-labels",
            "stage": "awaiting_confirmation",
            "payload": {},
            "updated_at": "2026-07-16T00:01:00Z",
        }
    ]
    revision = [
        {
            "service_name": "data_query",
            "version": 1,
            "turn_id": "turn-1",
            "snapshot_id": "snapshot-revision",
            "stage": "retrieving",
            "payload": {"revision_query": "改查去年"},
            "updated_at": "2026-07-16T00:02:00Z",
        }
    ]
    revised = merge_service_states(awaiting, revision)
    assert revised == revision

    new_turn = [
        {
            "service_name": "data_query",
            "version": 1,
            "turn_id": "turn-2",
            "snapshot_id": "snapshot-turn-2",
            "stage": "idle",
            "payload": {},
            "updated_at": "2026-07-16T00:03:00Z",
        }
    ]
    stale_old_turn = [
        {
            "service_name": "data_query",
            "version": 1,
            "turn_id": "turn-1",
            "snapshot_id": "snapshot-revision",
            "stage": "succeeded",
            "payload": {"sql_result": {"rows": [{"forged": True}]}},
            "updated_at": "2026-07-16T00:04:00Z",
        }
    ]

    assert merge_service_states(new_turn, stale_old_turn) == new_turn


def test_service_state_reducer_clear_marker_removes_service() -> None:
    """显式 clear 标记只清理对应业务状态。"""
    existing = [
        {
            "service_name": "data_query",
            "version": 1,
            "snapshot_id": "snapshot-old",
            "stage": "failed",
            "payload": {},
            "updated_at": "2026-07-16T00:00:00Z",
        }
    ]

    merged = merge_service_states(existing, [{"service_name": "data_query", "clear": True}])

    assert merged == []


def test_unknown_data_query_state_version_degrades_without_breaking_history() -> None:
    """未知 DataAgent 状态版本只能只读降级，不能让整个线程 checkpoint 加载失败。"""
    merged = merge_service_states(
        [],
        [
            {
                "service_name": "data_query",
                "version": 99,
                "turn_id": "turn-legacy",
                "snapshot_id": "snapshot-legacy",
                "stage": "approved",
                "payload": {"secret_future_field": "must-not-be-trusted"},
            }
        ],
    )

    assert merged == [
        {
            "service_name": "data_query",
            "version": 99,
            "turn_id": "turn-legacy",
            "snapshot_id": "snapshot-legacy",
            "stage": "unsupported_version",
            "payload": {"error_code": "DATA_QUERY_STATE_VERSION_UNSUPPORTED", "read_only": True},
        }
    ]
