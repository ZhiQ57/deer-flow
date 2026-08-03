# DataAgent Text2SQL 运行说明

当前 DataAgent 只有一条生产路径：继续使用 DeerFlow 原生 `lead_agent + agent_name` 运行 custom-agent，通过 `AgentConfig.service_ability` 动态装配 DataAgent 专属工具和 middleware。历史 `backend/packages/harness/deerflow-dev/` 实验运行层已经删除，不再作为生产、测试、调试或迁移入口。

## 入口

- 模板：`docs/agents/data-agent/config.yaml` 与 `docs/agents/data-agent/SOUL.md`。
- 运行：前端/SDK 仍使用 `assistantId=lead_agent`，运行上下文中的 `agent_name=data-agent` 触发 custom-agent 加载。
- 能力：`service_ability.type=data_query`、`version=1`、`enable_sql_rag=true` 时，由 `deerflow.agents.service_agent.registry.resolve_service_ability()` 注入 `publish_query_labels`、`ask_intent_approval`、`QueryLabelsMiddleware`、`QueryIntentApprovalMiddleware` 和 `SqlStageMiddleware`。
- 停用：删除整个 `service_ability`。v1 不保留 label-only 半能力。

## 检索

`sqlrag_retrieve` 仍是 DataAgent 使用的 TableRAG MCP 工具名，MCP Server 必须配置 `tool_name_prefix=false`。检索结果不再进入 `TableRAG` middleware、retrieval registry 或 `service_states.payload.retrieval`；它只按 DeerFlow 原生工具机制作为 `ToolMessage` 进入模型上下文。

这意味着失败、空结果和多次重试都对模型可见，下一步是否改写 query、换 operation、继续检索、发布标签或向用户解释缺口，由模型基于完整上下文自行判断。

## 状态

生产 DataAgent 不向 `ThreadState` 顶层新增 `data_agent_stage`、`data_query_labels`、`data_retrieval_context`、`data_sql_validation` 或 `data_sql_execution` 等平行业务字段。持久业务授权只放在 `ThreadState.service_states`：

- `publish_query_labels` 写入 `labels`、`approval_policy`、`review_items`。
- `ask_intent_approval` 写入人工确认请求、确认结果或取消状态。
- `SqlStageMiddleware` 只读取已发布/已确认的 label snapshot 与 Gateway 注入的 `data_query_binding`。
- SQL 结果以 `data_query_sql_result` ToolMessage artifact 为权威来源，前端按 artifact 渲染。

检索上下文不做服务端合并，也不做隐藏清洗；模型直接读取历史 `sqlrag_retrieve` ToolMessage。

## SQL 执行边界

正式 SQL 执行只在 Gateway 层：

- `app.gateway.modules.sql_execution` 负责 binding、DSN/Secret 解析、sqlglot 校验、数据库驱动、只读事务、结果预算、错误脱敏、run capability、路由和 SQL SubAgent 工具提供器。
- `deerflow-harness` 不导入 Gateway SQL 模块，不解析 DSN/Secret，不携带数据库驱动，也不直接执行业务 SQL。
- Gateway 只向 Harness runtime context 注入无密钥 `data_query_binding`。
- SQL SubAgent 工具通过内部路由 `/api/internal/threads/{id}/sql/{validate,execute}` 执行，普通前端不会直接调用内部接口。

公开手动执行接口仍是：

```text
POST /api/threads/{thread_id}/sql/execute
```

该接口只接受 `source=manual_ui`，按当前认证用户、线程 owner 和 custom-agent 配置解析数据源，不接受 DSN、Secret、Schema、run_id、snapshot_id 或 SubAgent 内部来源。

## 配置片段

```yaml
service_ability:
  type: data_query
  version: 1
  enable_sql_rag: true
  table_rag_config: tablerag.yaml
  data_source_id: text2sql-mysql-local
  source_binding_mode: logical_data_source
  confirmation_mode: on_ambiguity
  min_auto_confidence: 0.85
  sql_subagent_name: sql-subagent
  sql_execution:
    enabled: true
    database_type: mysql
    dsn_env: DATA_AGENT_MYSQL_DSN
    readonly: true
    max_execution_attempts: 3
    allowed_schemas: [text2sql]
```

`sql-subagent` 必须在根 `config.yaml -> subagents.custom_agents` 中显式注册，并且工具白名单只允许 `data_validate_sql` / `data_execute_sql`，不要给 SQL SubAgent 加载 `table-rag-agent` Skill。

## 验证

```powershell
Set-Location "D:\A-PythonWork\AOpenGithub\deer-flow\backend"
uv run pytest tests/test_query_labels_tool.py tests/test_data_agent_service_ability.py tests/test_data_agent_sqlrag_adapter.py tests/test_sql_executor.py tests/test_data_agent_database_e2e.py -q
uv run ruff check
```
