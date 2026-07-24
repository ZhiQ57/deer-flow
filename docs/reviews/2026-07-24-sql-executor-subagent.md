# SQL Executor 与 SQL SubAgent 执行链路 Review

## 1. Review 范围

本次实现范围：

- 正式 Harness 中的共享 `SqlExecutionService`。
- PostgreSQL/MySQL 只读 SQL 校验和执行。
- SQL 结果预算、数据源绑定和 Secret 脱敏。
- SQL SubAgent 的 `data_validate_sql` / `data_execute_sql` 工具会话。
- SQL 执行失败后的结构化错误反馈、重新校验和有限重试。
- DataAgent 父流程的 Snapshot、Binding、Artifact 和 ToolMessage 可信投影。

本次未实现：

- Gateway SQL Router。
- 前端 SQL 执行按钮和 SQL Result Panel。
- `manual_ui` 校验上下文。
- 独立 Query Executor 内网服务或 HTTP/RPC Provider。
- Analysis SubAgent、Chart SubAgent 和完整 Agent Contract。

## 2. 实现结果

### 2.1 共享执行入口

新增：

```text
backend/packages/harness/deerflow/agents/service_agent/sql_executor.py
```

公开类型和入口：

- `SqlValidationRequest`
- `SqlValidationResult`
- `SqlExecutionRequest`
- `SqlExecutionResult`
- `SqlExecutionService.validate()`
- `SqlExecutionService.execute()`
- `SqlExecutionService.aexecute()`

`sql_tools.py` 不再维护数据库驱动、AST 校验和结果转换代码，只负责当前 Snapshot 的 SQL SubAgent 工具装配与执行会话。

### 2.2 SQL SubAgent 执行闭环

```text
父 DataAgent
  -> task(sql-subagent)
  -> data_validate_sql
  -> data_execute_sql
  -> 执行错误
  -> SQL SubAgent 修复 SQL
  -> data_validate_sql
  -> data_execute_sql
  -> 成功或达到执行预算
  -> ToolMessage
  -> 父 DataAgent 可信结果投影
```

执行会话规则：

- 每次有效 `data_validate_sql` 生成一个新的 validation generation。
- `data_execute_sql` 只能消费当前未使用的 generation。
- 执行失败后必须重新调用 `data_validate_sql`。
- `sql_execution.max_execution_attempts` 控制数据库执行次数，默认 3，范围 1-5。
- 执行成功后禁止继续执行。
- `sql_only` 快照不装配 `data_execute_sql`。

### 2.3 错误合同

可修复错误返回：

- `error_category`
- `error_message`（仅修复类错误，最多 500 字符并完成凭据脱敏）
- `retryable`
- `recommended_action`

支持的修复决策包括：

- `syntax_error` → `repair_sql`
- `unknown_column` → `repair_sql`
- `unknown_table` → `repair_or_request_evidence`
- `ambiguous_column` → `repair_sql`
- `type_mismatch` → `repair_sql`
- `timeout` → `simplify_sql`
- `connection_error` → `retry_same_sql`
- `permission_denied`、配置错误、绑定错误 → `stop`

不会向模型、ToolMessage、Artifact 或日志返回 DSN、密码、Token 或异常堆栈。

## 3. 关键文件

| 文件 | 变更 |
|---|---|
| `backend/packages/harness/deerflow/agents/service_agent/sql_executor.py` | 新增共享 SQL Executor |
| `backend/packages/harness/deerflow/agents/service_agent/sql_tools.py` | 改为 Snapshot-scoped SQL 工具会话 |
| `backend/packages/harness/deerflow/agents/service_agent/sql_stage_middleware.py` | 使用共享 Service 重新校验子代理结果，注入执行预算和修复指令 |
| `backend/packages/harness/deerflow/agents/service_agent/config.py` | 新增 `max_execution_attempts` |
| `contracts/data_query/sql_subagent.v1.schema.json` | 补充执行预算和结构化错误字段 |
| `config.example.yaml` | 更新 SQL SubAgent 重试 Prompt 和 `max_turns=20` |
| `docs/guide/used-api.md` | 更新 SQL Executor 入口和调用约束 |
| `backend/tests/test_sql_executor.py` | 新增 Executor、错误分类、重试和可信投影测试 |

## 4. 验证结果

通过：

- `tests/test_sql_executor.py`：11 passed。
- `tests/test_data_agent_query_flow.py`：43 passed。
- `tests/test_data_agent_service_ability.py`：29 passed。
- `tests/test_task_tool_core_logic.py`：43 passed。
- `tests/test_harness_boundary.py`、`test_data_agent_state_persistence.py`、`test_data_agent_sqlrag_adapter.py`：16 passed。
- Ruff check：通过。
- Ruff format check：通过。
- Python compileall：通过。
- SQL SubAgent 真实数据库 E2E：1 skipped，原因是当前环境未提供 TableRAG/MySQL 所需环境变量。

### 4.1 基线问题

下列问题不属于本次 SQL Executor 改动：

- `test_lead_agent_model_resolution.py` 中 3 个测试依赖当前分支已移除的 lead-agent helper 或旧的 `load_agent_config` mock 签名。
- `test_custom_agent.py::TestAgentsAPI::test_create_agent_with_model_and_tool_groups` 返回 422。
- 全量收集包含废弃 `deerflow-dev` 测试，因缺少 `_load_enabled_skills_for_tool_policy` 阻断收集；该目录按项目约束不在本次范围。
- Windows 全量收集曾出现 `test_skillscan_native.py` 文件描述符读取异常；被测试过程异常删除的工作区文件已从基线恢复。
- 排除废弃目录后的全量测试超过 15 分钟未完成并被终止，未将其结果作为本次 SQL 验收依据。

## 5. 后续开发入口

下一阶段应新增 Gateway Router，并直接复用：

```text
Gateway Router -> SqlExecutionService
data_execute_sql -> SqlExecutionService
```

Gateway 完成后，再开发前端 SQL 代码块执行按钮和右侧 SQL Result Panel。届时不要在 `app.*` 或前端重新实现数据库连接、SQL 校验和结果预算。
