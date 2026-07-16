"""DataAgent 正式闭环真实 PostgreSQL TableRAG + MySQL 业务库 E2E。"""

from __future__ import annotations

import json
import os

import pytest
from table_rag.mcp.service import TableRAGMCPService
from table_rag.mcp.settings import TableRAGMCPSettings

from deerflow.agents.service_agent.binding import resolve_data_source_binding
from deerflow.agents.service_agent.config import DataQueryServiceAbilityConfig
from deerflow.agents.service_agent.sql_tools import build_sql_tools, execute_sql, validate_sql
from deerflow.agents.service_agent.state import build_retrieval_context


def _quote_mysql_identifier(value: str) -> str:
    """按 MySQL 规则引用来自 TableRAG registry 的标识符。"""
    return "`" + value.replace("`", "``") + "`"


def _real_environment() -> tuple[str, str, str, str]:
    """读取真实 E2E 所需环境变量，缺少时安全跳过。"""
    config_path = os.environ.get("TABLERAG_MCP_CONFIG") or os.environ.get("TABLERAG_CONFIG")
    index_dsn = os.environ.get("TABLERAG_MCP_INDEX_DSN")
    source_dsn = os.environ.get("TABLERAG_MCP_SOURCE_DSN")
    execution_dsn = os.environ.get("DATA_AGENT_MYSQL_DSN")
    if not all((config_path, index_dsn, source_dsn, execution_dsn)):
        pytest.skip("需要 TABLERAG 配置、PostgreSQL 索引 DSN 和 DATA_AGENT_MYSQL_DSN。")
    return config_path, index_dsn, source_dsn, execution_dsn


def test_real_tablerag_retrieval_and_readonly_mysql_execution() -> None:
    """验证真实 PostgreSQL 索引候选可经正式门禁在 MySQL 只读事务中执行。"""
    config_path, _, _, _ = _real_environment()
    settings = TableRAGMCPSettings.from_env()
    service = TableRAGMCPService(settings)
    index_validation = service.validate_index()
    assert index_validation["ok"] is True, index_validation.get("error")

    response = service.search_columns("女性因素诊断", column_top_k=20)
    assert response["ok"] is True, response.get("error")
    candidates = response.get("result") or []
    candidate = next(
        (item for item in candidates if isinstance(item, dict) and isinstance(item.get("table_name"), str) and isinstance(item.get("column_name"), str)),
        None,
    )
    assert candidate is not None, "TableRAG 没有返回可执行的表字段候选。"
    table_name = candidate["table_name"]
    column_name = candidate["column_name"]

    ability = DataQueryServiceAbilityConfig.model_validate(
        {
            "type": "data_query",
            "version": 1,
            "enable_sql_rag": True,
            "table_rag_config": config_path,
            "data_source_id": "text2sql-mysql-local",
            "source_binding_mode": "logical_data_source",
            "confirmation_mode": "auto",
            "sql_subagent_name": "sql-subagent",
            "sql_execution": {
                "enabled": True,
                "database_type": "mysql",
                "dsn_env": "DATA_AGENT_MYSQL_DSN",
                "readonly": True,
                "statement_timeout_seconds": 10,
                "max_rows": 5,
                "max_cell_chars": 200,
                "max_result_chars": 20_000,
                "allowed_schemas": ["text2sql"],
                "allowed_tables": [table_name, f"text2sql.{table_name}"],
                "allowed_columns": [column_name, f"{table_name}.{column_name}"],
            },
        }
    )
    binding = resolve_data_source_binding(ability)
    assert binding["retrieval_target_fingerprint"] != binding["execution_target_fingerprint"]
    assert binding["binding_fingerprint"].startswith("sha256:")

    retrieval = build_retrieval_context(
        response,
        tool_name="tablerag_search_columns",
        turn_id="database-e2e-turn",
        data_source_id=ability.data_source_id,
        binding=binding,
    )
    sql = f"SELECT {_quote_mysql_identifier(column_name)} FROM {_quote_mysql_identifier('text2sql')}.{_quote_mysql_identifier(table_name)} LIMIT 5"
    validation = validate_sql(
        sql,
        config=ability,
        retrieval=retrieval,
        snapshot_id="database-e2e-snapshot",
    )
    assert validation["valid"] is True, validation
    assert validation["database_type"] == "mysql"

    execution = execute_sql(
        validation["executable_sql"],
        validation_digest=validation["validation_digest"],
        validation=validation,
        config=ability,
    )
    assert execution["ok"] is True, execution
    assert execution["returned_row_count"] <= 5
    assert execution["row_count"] <= 5
    assert execution["columns"] == [column_name]
    assert len(json.dumps(execution["rows"], ensure_ascii=False, default=str)) <= ability.sql_execution.max_result_chars
    assert isinstance(execution["duration_ms"], float | int)

    assert validate_sql(f"DELETE FROM `{table_name}`", config=ability, retrieval=retrieval)["error_code"] == "SQL_READONLY_REQUIRED"
    assert validate_sql(f"SELECT `{column_name}` FROM `{table_name}`; SELECT 1", config=ability, retrieval=retrieval)["error_code"] == "SQL_MULTIPLE_STATEMENTS"
    assert validate_sql("SELECT table_name FROM information_schema.tables", config=ability, retrieval=retrieval)["error_code"] == "SQL_SYSTEM_DATABASE_FORBIDDEN"
    assert validate_sql(f"SELECT SLEEP(1), `{column_name}` FROM `{table_name}`", config=ability, retrieval=retrieval)["error_code"] == "SQL_DANGEROUS_FUNCTION"

    tools = build_sql_tools(
        ability,
        {
            "snapshot_id": "database-e2e-snapshot",
            "payload": {"retrieval": retrieval, "approval": {"status": "approved", "action": "execute"}},
        },
    )
    tool_validation = json.loads(tools[0].invoke({"sql": sql}))
    first = json.loads(
        tools[1].invoke(
            {
                "sql": tool_validation["executable_sql"],
                "validation_digest": tool_validation["validation_digest"],
            }
        )
    )
    second = json.loads(
        tools[1].invoke(
            {
                "sql": tool_validation["executable_sql"],
                "validation_digest": tool_validation["validation_digest"],
            }
        )
    )
    assert first["ok"] is True, first
    assert second["error_code"] == "SQL_EXECUTION_ALREADY_ATTEMPTED"
