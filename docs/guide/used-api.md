# Used API Guide

## DataAgent 生产 Text2SQL

- **正式入口**：Agent 运行仍使用 `assistantId=lead_agent`，通过运行上下文 `agent_name` 加载 custom-agent；不新增 DataAgent 专属 LangGraph、checkpoint 或 SSE 协议。
- **能力启用**：`AgentConfig.service_ability` 是唯一扩展入口。`type=data_query`、`version=1`、`enable_sql_rag=true` 时，`deerflow.agents.service_agent.registry.resolve_service_ability()` 动态提供 DataAgent 工具和 middleware。停用时删除整个 `service_ability`。
- **MCP 检索**：`sqlrag_retrieve` 是唯一启用的 TableRAG MCP 工具名。检索结果直接以原生 `ToolMessage` 进入模型上下文，不登记 `service_states.payload.retrieval`、Evidence registry 或阶段门禁；失败/空结果也保持可见，让模型自行决定重试、换 operation、改问或结束。
- **状态入口**：`ThreadState.service_states` 是唯一业务授权状态。生产 DataAgent 不向 `ThreadState` 顶层新增 `data_agent_stage`、`data_query_labels`、`data_retrieval_context` 或 SQL 平行字段。
- **标签工具**：`publish_query_labels` 只由 DataAgent service ability 提供，不在全局 `BUILTIN_TOOLS`。它发布标签 snapshot、`approval_policy` 和审核项，不要求先存在检索状态。
- **确认协议**：需要人工确认时，模型调用 DataAgent-only `ask_intent_approval`。后端复用 human-input v1，把请求和最终人工响应写回真实 `ToolMessage` 与 `service_states`。
- **SQL SubAgent**：父 Agent 只能通过现有 `task` 委派 `service_ability.sql_subagent_name`。`SqlStageMiddleware` 将 task prompt 替换为严格 JSON envelope，并要求当前 snapshot 已获批准或策略自动放行。
- **数据源绑定**：Gateway 生成无密钥 `data_query_binding` 注入 Harness runtime context。DSN、`dsn_env`、`secret://` 引用和请求级 Secret 都只留在 Gateway。
- **Run 上下文生命周期**：`prepare_sql_execution_run_context()` 在 Run 持久化准入前解析绑定并返回待注册上下文；`register_sql_execution_run_context()` 在取得 `run_id` 后同步登记，任务完成回调通过 `release_sql_execution_run_context()` 覆盖成功、失败和取消释放。持久化准入与任务挂载之间禁止新增 `await`。
- **Gateway SQL 模块**：`app.gateway.modules.sql_execution` 负责 contracts、binding、validator、drivers、result_budget、error_classifier、runtime_registry、run_context、router 和 tool_provider。`deerflow-harness` 不解析 DSN/Secret，不导入数据库驱动/sqlglot，不执行业务 SQL。
- **公开 SQL API**：`POST /api/threads/{thread_id}/sql/execute` 只接受 `source=manual_ui`，按当前认证用户、线程 owner 和 custom-agent 配置解析数据源；不接受内部认证头、run_id、snapshot_id、DSN、Secret、Schema 或 retrieval。
- **内部 SQL API**：SQL SubAgent 工具只通过 Gateway trusted internal route 调用 `/api/internal/threads/{thread_id}/sql/validate` 与 `/execute`。请求体只包含候选 SQL、run/snapshot 身份和 validation digest。
- **结果权威来源**：SQL 结果以 `data_query_sql_result` ToolMessage artifact 为准，content 只是模型可见 fallback。前端也从 artifact 渲染 QueryIntentCard / QueryResultCard。

## 配置引用

- Custom-agent 模板：`docs/agents/data-agent/config.yaml`
- SQL SubAgent 示例：`config.example.yaml -> subagents.custom_agents.sql-subagent`
- TableRAG MCP：`extensions_config.example.json -> mcpServers.tablerag`，必须设置 `tool_name_prefix=false`
- 执行 DSN：`service_ability.sql_execution.dsn_env` 指向的环境变量或 Gateway Secret 引用，真实 DSN 不进入 Git、artifact、checkpoint 或日志

## 已删除旧入口

`backend/packages/harness/deerflow-dev` 实验运行层、旧 DataAgent 顶层状态、独立图工厂和本地调试脚本已经删除。后续不要恢复 `TableRagStageMiddleware`、retrieval registry、`data_retrieval_context` 或 TableRAG 阶段门禁。
